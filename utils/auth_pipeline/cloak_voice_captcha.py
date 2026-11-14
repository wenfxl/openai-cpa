from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Iterable, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VOSK_MODEL = PROJECT_ROOT / "models" / "vosk-model-small-en-us-0.15"
DEFAULT_SHERPA_MODEL = (
    PROJECT_ROOT / "models" / "sherpa-onnx-streaming-zipformer-en-2023-06-26-int8"
)
VOSK_DIGIT_GRAMMAR = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "oh",
    "[unk]",
]
DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
SHERPA_DIGIT_WORDS = {
    **DIGIT_WORDS,
    "zero": "0",
    "hero": "0",
    "nero": "0",
    "nehemo": "0",
    "nemo": "0",
    "zeal": "0",
    "now": "0",
    "state": "8",
    "kew": "2",
    "kei": "2",
}


class VoiceCaptchaError(RuntimeError):
    """语音验证码无法可靠识别时抛出。"""


def _find_ffmpeg() -> str:
    configured = str(os.environ.get("CLOAK_FFMPEG_PATH") or "").strip()
    candidates = [
        configured,
        shutil.which("ffmpeg") or "",
        r"D:\ffmpeg-5.0.1-essentials_build\bin\ffmpeg.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise VoiceCaptchaError(
        "缺少 ffmpeg，无法把语音验证码转换为 16kHz 单声道 WAV"
    )


def _convert_audio(source: Path, destination: Path) -> Path:
    return _convert_audio_filter(
        source,
        destination,
        "highpass=f=80,loudnorm=I=-16:LRA=11:TP=-1.5",
    )


def _convert_audio_filter(
    source: Path,
    destination: Path,
    audio_filter: str,
) -> Path:
    ffmpeg = _find_ffmpeg()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        audio_filter,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if completed.returncode != 0 or not destination.is_file():
        detail = (completed.stderr or completed.stdout or "").strip()
        raise VoiceCaptchaError(f"音频转换失败: {detail[:500]}")
    return destination


def _load_pcm(path: Path) -> tuple[int, bytes]:
    try:
        with wave.open(str(path), "rb") as handle:
            if handle.getnchannels() != 1:
                raise VoiceCaptchaError("语音验证码 WAV 不是单声道")
            if handle.getsampwidth() != 2:
                raise VoiceCaptchaError("语音验证码 WAV 不是 16-bit PCM")
            rate = int(handle.getframerate())
            if rate != 16000:
                raise VoiceCaptchaError(f"语音验证码 WAV 采样率不是 16kHz: {rate}")
            return rate, handle.readframes(handle.getnframes())
    except (OSError, wave.Error) as exc:
        raise VoiceCaptchaError(f"无法读取语音验证码 WAV: {exc}") from exc


def _digit_observations(words: Iterable[dict]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    for item in words:
        word = str(item.get("word") or "").strip().lower()
        digit = DIGIT_WORDS.get(word)
        if not digit:
            continue
        try:
            confidence = float(item.get("conf", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        if confidence < 0.35:
            continue
        result.append((digit, confidence))
    return result


def _repeated_half_code(
    observations: list[tuple[str, float]],
) -> tuple[Optional[str], str]:
    digits = "".join(digit for digit, _confidence in observations)
    if len(digits) < 4:
        return None, f"too few digits: {digits!r}"
    if len(digits) % 2:
        return None, f"odd digit count: {len(digits)}"
    midpoint = len(digits) // 2
    first = digits[:midpoint]
    second = digits[midpoint:]
    if first != second:
        return None, f"halves differ: {first!r} != {second!r}"
    return first, f"repeated {first!r}"


def _recognize_payload(
    model: Any,
    rate: int,
    pcm: bytes,
    grammar: list[str],
) -> list[tuple[str, float]]:
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(model, rate, json.dumps(grammar))
    recognizer.SetWords(True)
    recognizer.AcceptWaveform(pcm)
    payload = json.loads(recognizer.FinalResult())
    return _digit_observations(payload.get("result") or [])


def _recognize_alternatives(
    model: Any,
    rate: int,
    pcm: bytes,
) -> list[list[tuple[str, float]]]:
    from vosk import KaldiRecognizer

    recognizer = KaldiRecognizer(
        model,
        rate,
        json.dumps(VOSK_DIGIT_GRAMMAR),
    )
    recognizer.SetWords(True)
    recognizer.SetMaxAlternatives(20)
    recognizer.AcceptWaveform(pcm)
    payload = json.loads(recognizer.FinalResult())
    alternatives = payload.get("alternatives") or []
    if not alternatives:
        alternatives = [payload]
    return [
        _digit_observations(item.get("result") or [])
        for item in alternatives
    ]


def _repeat_candidate(sequence: str) -> tuple[Optional[str], str]:
    return _repeated_half_code(
        [(digit, 1.0) for digit in sequence]
    )


def _vote_code(
    passes: Iterable[list[tuple[str, float]]],
) -> tuple[Optional[str], str]:
    observed = list(passes)
    exact: dict[str, int] = {}
    for observations in observed:
        sequence = "".join(digit for digit, _confidence in observations)
        code, reason = _repeat_candidate(sequence)
        if code and len(sequence) == len(code) * 2:
            exact[code] = exact.get(code, 0) + 1
    if exact:
        ordered = sorted(exact.items(), key=lambda item: item[1], reverse=True)
        if len(ordered) == 1 or ordered[0][1] > ordered[1][1]:
            return ordered[0][0], f"exact consensus: {ordered!r}"
        return None, f"conflicting exact candidates: {ordered!r}"
    return None, "no exact repeated half found"


def _sherpa_model_files(
    model_dir: Path,
) -> tuple[Path, Path, Path, Path]:
    tokens = model_dir / "tokens.txt"
    encoders = sorted(model_dir.glob("encoder-*.int8.onnx"))
    decoders = sorted(model_dir.glob("decoder-*.int8.onnx"))
    joiners = sorted(model_dir.glob("joiner-*.int8.onnx"))
    if not tokens.is_file() or not encoders or not decoders or not joiners:
        raise VoiceCaptchaError(f"Sherpa 英文数字模型不完整: {model_dir}")
    return tokens, encoders[0], decoders[0], joiners[0]


def _recognize_sherpa_text(
    recognizer: Any,
    pcm: bytes,
    *,
    rate: int,
) -> str:
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    stream = recognizer.create_stream()
    stream.accept_waveform(rate, samples)
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    return str(recognizer.get_result(stream) or "")


def _sherpa_digits(text: str) -> str:
    digits: list[str] = []
    for word in re.findall(r"[a-z]+", text.lower()):
        digit = SHERPA_DIGIT_WORDS.get(word)
        if digit is not None:
            digits.append(digit)
    return "".join(digits)


def _align_sherpa_halves(
    first: str,
    second: str,
) -> tuple[Optional[str], dict[str, Any]]:
    if first and first == second:
        return first, {"mode": "exact", "first": first, "second": second}
    if not first or not second or len(first) != len(second):
        return None, {
            "mode": "length-mismatch",
            "first": first,
            "second": second,
        }
    for index, (left, right) in enumerate(zip(first, second)):
        if left == right:
            continue
        prefix = first[:index]
        suffix = first[index + 1:]
        if prefix + right + suffix != second:
            return None, {
                "mode": "multiple-mismatch",
                "first": first,
                "second": second,
            }
    return first, {"mode": "single-position", "first": first, "second": second}


def _align_sherpa_variants(
    variants: list[tuple[str, str]],
) -> tuple[Optional[str], dict[str, Any]]:
    exact: dict[str, list[str]] = {}
    for label, digits in variants:
        current, alignment = _align_sherpa_halves(digits, digits)
        if current:
            exact.setdefault(current, []).append(label)
    ordered = sorted(
        exact.items(),
        key=lambda item: (len(item[1]), len(item[0])),
        reverse=True,
    )
    if ordered:
        code, labels = ordered[0]
        return code, {
            "mode": "exact-halves",
            "variants": variants,
            "support": labels,
            "conflict": len(ordered) > 1,
        }
    return None, {
        "mode": "conflicting-variants",
        "variants": variants,
    }


def _recognize_sherpa_halves(
    source: Path,
    *,
    model_path: Optional[str | Path] = None,
) -> tuple[Optional[str], list[dict[str, Any]]]:
    model_dir = Path(model_path) if model_path else DEFAULT_SHERPA_MODEL
    if not model_dir.is_dir():
        raise VoiceCaptchaError(f"缺少 Sherpa 英文数字语音模型: {model_dir}")
    try:
        import sherpa_onnx
    except ImportError as exc:
        raise VoiceCaptchaError(
            "缺少 sherpa-onnx 语音识别依赖，请先安装 requirements.txt"
        ) from exc

    tokens, encoder, decoder, joiner = _sherpa_model_files(model_dir)
    recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=str(tokens),
        encoder=str(encoder),
        decoder=str(decoder),
        joiner=str(joiner),
        num_threads=2,
        sample_rate=16000,
        feature_dim=80,
        decoding_method="greedy_search",
        max_active_paths=4,
    )
    output: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="cloak-voice-halves-") as workdir:
        workdir_path = Path(workdir)
        for label, audio_filter in (
            ("base", "anull"),
            ("hp80", "highpass=f=80"),
            ("afftdn", "afftdn=nf=-30"),
        ):
            normalized = workdir_path / f"{label}-16k.wav"
            _convert_audio_filter(source, normalized, audio_filter)
            rate, pcm = _load_pcm(normalized)
            # 易盾 plays the same code twice, separated by a long pause.
            split = int(len(pcm) / 2)
            split -= split % 2
            halves = (pcm[:split], pcm[split:])
            texts = [
                _recognize_sherpa_text(recognizer, half, rate=rate)
                for half in halves
            ]
            digits = [_sherpa_digits(text) for text in texts]
            output.append(
                {
                    "label": label,
                    "texts": texts,
                    "digits": digits,
                }
            )

    half_results: list[tuple[str, str]] = []
    for item in output:
        first, second = item["digits"]
        candidate, alignment = _align_sherpa_halves(first, second)
        item["alignment"] = alignment
        if candidate:
            half_results.append((str(item["label"]), candidate))
    if half_results:
        code, consensus = _align_sherpa_variants(half_results)
        if code:
            output.append({"consensus": consensus})
            return code, output
    return None, output


def recognize_voice_code(
    audio_path: str | Path,
    *,
    model_path: Optional[str | Path] = None,
    sherpa_model_path: Optional[str | Path] = None,
    debug_callback: Optional[Any] = None,
) -> str:
    source = Path(audio_path)
    if not source.is_file() or source.stat().st_size <= 0:
        raise VoiceCaptchaError("语音验证码音频不存在或为空")

    sherpa_code, sherpa_payload = _recognize_sherpa_halves(
        source,
        model_path=sherpa_model_path,
    )
    if sherpa_code:
        if debug_callback is not None:
            debug_callback(
                [
                    {
                        "recognizer": "sherpa",
                        **item,
                    }
                    for item in sherpa_payload
                ]
            )
        return sherpa_code

    try:
        from vosk import Model
    except ImportError as exc:
        raise VoiceCaptchaError(
            "缺少 vosk 语音识别依赖，请先安装 requirements.txt"
        ) from exc

    model_dir = Path(model_path) if model_path else DEFAULT_VOSK_MODEL
    if not model_dir.is_dir():
        raise VoiceCaptchaError(
            f"缺少英文数字语音模型: {model_dir}"
        )

    variants = [
        ("tempo110", "atempo=1.1"),
        ("base", "anull"),
        ("hp80", "highpass=f=80"),
        ("afftdn", "afftdn=nf=-30"),
    ]
    with tempfile.TemporaryDirectory(prefix="cloak-voice-") as workdir:
        workdir_path = Path(workdir)
        grammars = [
            [
                "zero", "one", "two", "three", "four", "five", "six",
                "seven", "eight", "nine", "[unk]",
            ],
            [
                "zero", "one", "two", "three", "four", "five", "six",
                "seven", "eight", "nine", "oh", "[unk]",
            ],
        ]
        passes: list[list[tuple[str, float]]] = []
        payloads: list[str] = []
        try:
            model = Model(str(model_dir))
            for label, audio_filter in variants:
                normalized = workdir_path / f"{label}-16k.wav"
                _convert_audio_filter(source, normalized, audio_filter)
                rate, pcm = _load_pcm(normalized)
                alternatives = _recognize_alternatives(model, rate, pcm)
                passes.extend(alternatives)
                payloads.extend(
                    "".join(digit for digit, _confidence in item)
                    for item in alternatives
                )
                for grammar in grammars:
                    observations = _recognize_payload(
                        model,
                        rate,
                        pcm,
                        grammar,
                    )
                    passes.append(observations)
                    payloads.append(
                        "".join(digit for digit, _confidence in observations)
                    )
        except Exception as exc:
            raise VoiceCaptchaError(f"语音识别器运行失败: {exc}") from exc

    if debug_callback is not None:
        debug_callback(
            [
                {
                    "digits": payload,
                    "observations": observations,
                }
                for payload, observations in zip(payloads, passes)
            ]
        )
    code, reason = _vote_code(passes)
    if code:
        return code

    raise VoiceCaptchaError(
        f"没有获得前后重复的数字验证码 ({reason})，"
        f"原始结果: {payloads!r}"
    )


def recognize_voice_code_strict(
    audio_path: str | Path,
    *,
    model_path: Optional[str | Path] = None,
) -> str:
    code = recognize_voice_code(audio_path, model_path=model_path)
    if not re.fullmatch(r"\d+", code):
        raise VoiceCaptchaError(f"识别结果不是纯数字验证码: {code!r}")
    return code

