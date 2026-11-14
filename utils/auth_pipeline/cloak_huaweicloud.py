from __future__ import annotations

import json
import queue
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from utils import config as cfg
from utils.email_providers.mail_service import (
    get_email_and_token,
    get_oai_code,
    mask_email,
    set_last_email,
)

from . import cloak_runtime as runtime
from .cloak_voice_captcha import VoiceCaptchaError, recognize_voice_code
from .user_utils import _generate_password


HUAWEI_START_URL = (
    "https://reg.huaweicloud.com/registerui/intl/index.html"
    "?locale=en-us&service=#/newRegister"
)
HUAWEI_SUCCESS_HOST = "account-intl.huaweicloud.com"
HUAWEI_SUCCESS_MARKER = "#/complete"
HUAWEI_AUDIO_ROOT = runtime.PROJECT_ROOT / "data" / "cloak_audio" / "huaweicloud"
HUAWEI_ERROR_MARKERS = (
    "verification code is incorrect",
    "invalid verification code",
    "verification code has expired",
    "email address is already registered",
    "failed to sign up",
    "please try again",
)

_log = runtime.log
_check_stop = runtime.check_stop
_human_pause = runtime.human_pause
_page_url = runtime.page_url
_safe_url_for_log = runtime.safe_url_for_log
_safe_evaluate = runtime.safe_evaluate
_human_input_value = runtime.human_input_value


class HuaweiAccountRejected(RuntimeError):
    """华为云拒绝本次注册请求时抛出。"""


def _text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _placeholder_locator(page: Any, value: str) -> Any:
    candidates = page.get_by_placeholder(value, exact=False)
    try:
        count = candidates.count()
    except Exception:
        count = 0
    for index in range(count):
        candidate = candidates.nth(index)
        try:
            rect = candidate.bounding_box()
            if rect and rect.get("width", 0) > 8 and rect.get("height", 0) > 8:
                return candidate
        except Exception:
            continue
    if count:
        return candidates.first
    return page.locator(f"input[placeholder={json.dumps(value)}]").first


def _page_snapshot(page: Any) -> dict:
    result = _safe_evaluate(
        page,
        r"""() => {
          const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
            && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
          const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
          return {
            url: location.href,
            title: document.title,
            body: clean(document.body && document.body.innerText).slice(0, 5000),
            controls: [...document.querySelectorAll('input,textarea,select,button')]
              .filter(visible)
              .map(el => ({
                tag: String(el.tagName || '').toLowerCase(),
                type: String(el.type || ''),
                id: String(el.id || ''),
                name: String(el.name || ''),
                placeholder: String(el.placeholder || ''),
                text: clean(el.innerText || el.textContent || el.value).slice(0, 180),
                valueLength: String(el.value || '').length,
                disabled: !!el.disabled
              })),
            errors: [...document.querySelectorAll(
              "[role='alert'],[aria-live],[class*='error' i],[class*='invalid' i]"
            )].filter(visible).map(el => clean(el.innerText || el.textContent)).filter(Boolean)
          };
        }""",
    )
    return result if isinstance(result, dict) else {"url": _page_url(page)}


def _challenge_controls(page: Any) -> list[dict]:
    controls: list[dict] = []
    for frame_index, frame in enumerate(page.frames):
        try:
            items = _safe_evaluate(
                frame,
                r"""() => {
                  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                  return [...document.querySelectorAll(
                    "button,input,textarea,select,[role='button'],[role='checkbox'],[role='slider']," +
                    "[class*='slider' i],[class*='captcha' i],[class*='verify' i]"
                  )].filter(visible).map(el => ({
                    tag: String(el.tagName || '').toLowerCase(),
                    type: String(el.type || ''),
                    id: String(el.id || ''),
                    name: String(el.name || ''),
                    role: String(el.getAttribute('role') || ''),
                    text: clean(el.innerText || el.textContent || el.value).slice(0, 300),
                    className: String(el.className || '').slice(0, 300),
                    disabled: !!el.disabled
                  }));
                }""",
            )
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                item["frame"] = frame
                controls.append(item)
    return controls


def _try_huawei_verification(page: Any) -> bool:
    for control in _challenge_controls(page):
        text = " ".join(
            str(control.get(key) or "")
            for key in ("text", "role", "className", "id", "name")
        ).lower()
        if not any(word in text for word in ("verify", "验证", "confirm", "continue", "next")):
            continue
        frame = control.get("frame")
        try:
            if control.get("id"):
                locator = frame.locator(f"[id={json.dumps(str(control['id']))}]").first
            elif control.get("name"):
                locator = frame.locator(f"[name={json.dumps(str(control['name']))}]").first
            else:
                locator = frame.get_by_text(str(control.get("text") or ""), exact=False).first
            if runtime.human_click(locator):
                _human_pause(0.45, 1.0)
                return True
        except Exception:
            continue
    return False


def _open_voice_mode(page: Any) -> bool:
    selectors = (
        ".yidun_voice__switch",
        ".yidun_switch",
        ".yidun_voice-mode",
        ".yidun_voice__btn",
    )
    for frame in page.frames:
        for selector in selectors:
            try:
                button = frame.locator(selector).first
                if button.count() > 0 and button.is_visible(timeout=400):
                    if runtime.human_click(button, timeout=2500):
                        _human_pause(0.4, 0.8)
                        return True
            except Exception:
                continue
    for frame in page.frames:
        try:
            button = frame.get_by_text(
                re.compile(
                    r"^(?:audio|voice|listen)(?:\s+verification)?$",
                    re.IGNORECASE,
                )
            ).first
            if button.count() > 0 and button.is_visible(timeout=400):
                if runtime.human_click(button, timeout=2500):
                    _human_pause(0.4, 0.8)
                    return True
        except Exception:
            continue
    return False


def _click_text_button(page: Any, value: str) -> bool:
    try:
        locator = page.get_by_role(
            "button",
            name=re.compile(value, re.IGNORECASE),
        ).first
        if locator.is_visible(timeout=1200):
            return runtime.human_click(locator)
    except Exception:
        pass
    return False


def _verification_body(page: Any) -> str:
    chunks: list[str] = []
    for frame in page.frames:
        try:
            body = _safe_evaluate(
                frame,
                "() => String(document.body && document.body.innerText || '')",
            )
            if body:
                chunks.append(str(body))
        except Exception:
            continue
    return "\n".join(chunks)


def _verification_popup_visible(page: Any, snapshot: Optional[dict] = None) -> bool:
    body = str((snapshot or _page_snapshot(page)).get("body") or "")
    if "Please complete verification" in body:
        return True
    return "Please complete verification" in _verification_body(page)


def _wait_voice_input_ready(page: Any, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_stop()
        for frame in page.frames:
            try:
                voice_input = frame.locator(".yidun_voice__input").first
                if voice_input.count() > 0 and voice_input.is_visible(timeout=300):
                    return True
            except Exception:
                continue
        _try_huawei_verification(page)
        time.sleep(0.5)
    return False


def _voice_challenge_debug(page: Any, limit: int = 12) -> list[str]:
    result: list[str] = []
    for frame_index, frame in enumerate(page.frames):
        frame_url = ""
        frame_name = ""
        frame_title = ""
        frame_body = ""
        frame_errors: list[str] = []
        try:
            frame_url = runtime.safe_url_for_log(str(frame.url or ""))
        except Exception:
            pass
        try:
            frame_name = str(frame.name or "")[:80]
        except Exception:
            pass
        try:
            frame_info = _safe_evaluate(
                frame,
                r"""() => {
                  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                  return {
                    title: clean(document.title).slice(0, 100),
                    body: clean(document.body && document.body.innerText).slice(0, 240),
                    errors: [...document.querySelectorAll(
                      "[role='alert'],[class*='error' i],[class*='invalid' i]"
                    )].map(el => clean(el.innerText || el.textContent)).filter(Boolean).slice(0, 4)
                  };
                }""",
            )
            if isinstance(frame_info, dict):
                frame_title = _text(frame_info.get("title"), 100)
                frame_body = _text(frame_info.get("body"), 240)
                frame_errors = [
                    _text(item, 160)
                    for item in frame_info.get("errors") or []
                    if _text(item, 160)
                ]
        except Exception:
            pass
        if frame_url or frame_name or frame_title or frame_body or frame_errors:
            result.append(
                f"f{frame_index}:url={frame_url or '-'}:name={frame_name or '-'}:"
                f"title={frame_title or '-'}:body={frame_body or '-'}:"
                f"errors={frame_errors or '-'}"
            )
        try:
            items = _safe_evaluate(
                frame,
                r"""() => {
                  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                  return [...document.querySelectorAll(
                    "button,[role='button'],audio,[class*='yidun' i],[class*='play' i],[class*='audio' i]"
                  )].map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                      tag: String(el.tagName || '').toLowerCase(),
                      type: String(el.type || ''),
                      text: clean(el.innerText || el.textContent || el.value).slice(0, 80),
                      aria: String(el.getAttribute('aria-label') || '').slice(0, 80),
                      title: String(el.getAttribute('title') || '').slice(0, 80),
                      className: String(el.className || '').slice(0, 100),
                      width: Math.round(rect.width || 0),
                      height: Math.round(rect.height || 0),
                      visible: !!(rect.width || rect.height)
                        && getComputedStyle(el).visibility !== 'hidden'
                        && getComputedStyle(el).display !== 'none'
                    };
                  });
                }""",
            )
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            text = " ".join(
                str(item.get(key) or "")
                for key in ("text", "aria", "title", "className")
            ).lower()
            if not any(
                marker in text
                for marker in (
                    "voice",
                    "audio",
                    "play",
                    "yidun",
                    "verification",
                    "verify",
                )
            ):
                continue
            result.append(
                f"f{frame_index}:"
                f"{item.get('tag') or '?'}:"
                f"visible={bool(item.get('visible'))}:"
                f"{item.get('width')}x{item.get('height')}:"
                f"{text[:180]}"
            )
            if len(result) >= limit:
                return result
    return result


def _voice_challenge_controls_ready(page: Any) -> bool:
    for frame in page.frames:
        try:
            items = _safe_evaluate(
                frame,
                r"""() => {
                  const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
                  return [...document.querySelectorAll(
                    "audio,.yidun_voice__input,.yidun_audio__play," +
                    ".yidun_voice,.yidun_audio,.yidun_control," +
                    "[class*='yidun_voice' i],[class*='yidun_audio' i]," +
                    "[class*='audio__play' i],[class*='voice__input' i]"
                  )].map(el => {
                    const rect = el.getBoundingClientRect();
                    return {
                      text: clean(el.innerText || el.textContent || el.value).slice(0, 120),
                      aria: String(el.getAttribute('aria-label') || '').slice(0, 120),
                      title: String(el.getAttribute('title') || '').slice(0, 120),
                      className: String(el.className || '').slice(0, 140),
                      disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true'),
                      tag: String(el.tagName || '').toLowerCase(),
                      type: String(el.type || '').toLowerCase(),
                      width: Math.round(rect.width || 0),
                      height: Math.round(rect.height || 0),
                      visible: !!(rect.width || rect.height)
                        && getComputedStyle(el).visibility !== 'hidden'
                        && getComputedStyle(el).display !== 'none'
                    };
                  });
                }""",
            )
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("visible"):
                continue
            text = " ".join(
                str(item.get(key) or "")
                for key in ("text", "aria", "title", "className")
            ).lower()
            if item.get("disabled"):
                continue
            if any(
                marker in text
                for marker in (
                    "yidun_voice",
                    "yidun_audio",
                    "yidun_control",
                    "voice__input",
                    "audio__play",
                )
            ):
                return True
            if item.get("tag") == "audio":
                return True
    return False


def _play_voice_challenge(page: Any) -> bool:
    player_deadline = time.time() + 15.0
    while time.time() < player_deadline:
        _check_stop()
        for frame in page.frames:
            try:
                candidate = frame.locator(".yidun_audio__play").first
                if candidate.count() <= 0 or not candidate.is_visible(timeout=500):
                    continue
                if runtime.human_click(candidate, timeout=4500):
                    return True
                try:
                    candidate.evaluate("el => el.click()")
                    return True
                except Exception:
                    pass
            except Exception:
                continue
        _open_voice_mode(page)
        time.sleep(0.35)

    _try_huawei_verification(page)
    selectors = (
        ".yidun_audio__play",
        "[class*='audio'][class*='play' i]",
        "[class*='voice'][class*='play' i]",
        "button[aria-label*='voice' i]",
        "button[title*='voice' i]",
    )
    for frame in page.frames:
        for selector in selectors:
            try:
                player = frame.locator(selector).first
                if player.count() > 0 and player.is_visible(timeout=500):
                    if runtime.human_click(player, timeout=2500):
                        return True
            except Exception:
                continue
        try:
            player = frame.get_by_role(
                "button",
                name=re.compile(
                    r"play\s+(?:the\s+)?voice\s+verification\s+code",
                    re.IGNORECASE,
                ),
            ).first
            if player.count() > 0 and player.is_visible(timeout=500):
                if runtime.human_click(player, timeout=2500):
                    return True
        except Exception:
            pass
        try:
            player = frame.get_by_role(
                "button",
                name=re.compile(
                    r"(?:play|listen).*(?:voice|audio)|(?:voice|audio).*(?:play|listen)",
                    re.IGNORECASE,
                ),
            ).first
            if player.count() > 0 and player.is_visible(timeout=500):
                if runtime.human_click(player, timeout=2500):
                    return True
        except Exception:
            pass
        try:
            player = frame.get_by_text(
                re.compile(r"play\s+(?:the\s+)?voice", re.IGNORECASE)
            ).first
            if player.count() > 0 and player.is_visible(timeout=500):
                if runtime.human_click(player, timeout=2500):
                    return True
        except Exception:
            pass
    try:
        player = page.get_by_role(
            "button",
            name=re.compile(
                r"play\s+(?:the\s+)?voice\s+verification\s+code",
                re.IGNORECASE,
            ),
        ).first
        if player.count() > 0 and player.is_visible(timeout=1200):
            if runtime.human_click(player, timeout=2500):
                return True
    except Exception:
        pass
    _log(
        "华为云语音播放按钮定位失败，"
        f"url={_safe_url_for_log(_page_url(page))}, "
        f"frames={len(page.frames)}, "
        f"controls={_voice_challenge_debug(page)}"
    )
    return False


def _wait_for_voice_panel(page: Any, timeout: float = 30.0) -> bool:
    deadline = time.time() + max(5.0, timeout)
    while time.time() < deadline:
        _check_stop()
        if _voice_challenge_controls_ready(page):
            return True
        _open_voice_mode(page)
        time.sleep(0.4)
    return False


def _discard_cached_audio(audio_queue: queue.Queue) -> int:
    highest_sequence = 0
    while True:
        try:
            item = audio_queue.get_nowait()
        except queue.Empty:
            return highest_sequence
        if not isinstance(item, tuple) or len(item) < 1:
            continue
        sequence = item[0]
        highest_sequence = max(highest_sequence, int(sequence or 0))


def _response_started_ms(response: Any) -> float:
    request = getattr(response, "request", None)
    try:
        timing = request.timing if request is not None else None
    except Exception:
        timing = None
    if isinstance(timing, dict):
        try:
            started = float(timing.get("startTime") or 0)
        except (TypeError, ValueError):
            started = 0.0
        if started > 1_000_000_000_000:
            return started
    return time.time() * 1000.0


def _challenge_audio_response(
    response: Any,
    audio_queue: queue.Queue,
    workdir: Path,
    sequence: list[int],
    active: Optional[list[bool]] = None,
) -> None:
    try:
        if active is not None and not active[0]:
            return
        response_started_ms = _response_started_ms(response)
        url = str(response.url).lower()
        content_type = str(response.headers.get("content-type") or "").lower()
        if "audio" not in content_type and not url.endswith((".mp3", ".wav", ".ogg")):
            return
        body = response.body()
        if (
            not body
            or (active is not None and not active[0])
            or not workdir.is_dir()
        ):
            return
        sequence[0] += 1
        suffix = Path(url.split("?", 1)[0]).suffix or ".mp3"
        path = workdir / f"voice-{sequence[0]:02d}{suffix}"
        try:
            path.write_bytes(body)
        except FileNotFoundError:
            return
        if path.stat().st_size > 1024:
            audio_queue.put((sequence[0], path, response_started_ms))
    except FileNotFoundError:
        return
    except Exception as exc:
        _log(f"捕获华为云语音验证码响应失败: {exc}")
        return


def _page_audio_sources(page: Any) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for frame in page.frames:
        try:
            items = _safe_evaluate(
                frame,
                r"""() => [...document.querySelectorAll('audio,audio source')]
                  .map(el => String(el.currentSrc || el.src || ''))
                  .filter(Boolean)""",
            )
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            url = str(item or "").strip()
            if url and url not in seen:
                seen.add(url)
                sources.append(url)
    return sources


def _voice_audio_playing(page: Any) -> bool:
    for frame in page.frames:
        try:
            state = _safe_evaluate(
                frame,
                r"""() => {
                  const players = [...document.querySelectorAll('audio,video')];
                  if (!players.length) return null;
                  return players.some(el => !el.paused && !el.ended);
                }""",
            )
        except Exception:
            continue
        if state is True:
            return True
    return False


def _wait_for_voice_audio_playing(page: Any, timeout: float = 10.0) -> bool:
    deadline = time.time() + max(1.0, timeout)
    while time.time() < deadline:
        _check_stop()
        if _voice_audio_playing(page):
            return True
        time.sleep(0.2)
    return False


def _capture_page_audio(page: Any, workdir: Path) -> list[Path]:
    paths: list[Path] = []
    for index, url in enumerate(_page_audio_sources(page), start=1):
        try:
            response = page.request.get(url, timeout=30000)
            if not response.ok:
                continue
            body = response.body()
            if len(body) <= 1024:
                continue
            suffix = Path(str(url).split("?", 1)[0]).suffix or ".mp3"
            path = workdir / f"page-audio-{index:02d}{suffix}"
            path.write_bytes(body)
            paths.append(path)
        except Exception as exc:
            _log(f"读取页面播放器音频失败: {exc}")
    return paths


def _recognize_current_voice(
    page: Any,
    audio_queue: queue.Queue,
    after_sequence: int = 0,
    workdir: Optional[Path] = None,
) -> tuple[str, int]:
    audio_path: Optional[Path] = None
    if not _wait_for_voice_panel(page):
        raise RuntimeError("华为云语音验证面板未就绪，未点击播放")
    sequence_before = max(after_sequence, int(_discard_cached_audio(audio_queue)))
    click_started_ms = time.time() * 1000.0
    play_clicked = _play_voice_challenge(page)
    if not play_clicked:
        raise RuntimeError("华为云语音验证码必须先点击播放按钮")
    play_started = _wait_for_voice_audio_playing(page)
    if not play_started:
        raise RuntimeError("华为云语音播放按钮已点击，但播放器未开始播放")
    deadline = time.time() + 60.0
    next_page_probe = time.time() + 2.0
    while time.time() < deadline:
        _check_stop()
        try:
            sequence, path, started_ms = audio_queue.get(
                timeout=min(1.0, max(0.1, deadline - time.time()))
            )
        except queue.Empty:
            if (
                workdir is not None
                and play_started
                and time.time() >= next_page_probe
            ):
                captured = _capture_page_audio(page, workdir)
                if captured:
                    audio_path = captured[-1]
                    sequence = sequence_before + 1
                    break
                next_page_probe = time.time() + 3.0
            continue
        if (
            sequence > sequence_before
            and float(started_ms or 0) >= click_started_ms - 50.0
            and path.stat().st_size > 1024
        ):
            audio_path = path
            break
    if audio_path is None:
        raise RuntimeError("等待华为云语音验证码音频超时")

    try:
        code = recognize_voice_code(audio_path)
    except VoiceCaptchaError as exc:
        raise RuntimeError(f"华为云语音验证码识别失败: {exc}") from exc
    return code, sequence


def _submit_voice_code(page: Any, code: str) -> None:
    voice_frame = None
    voice_input = None
    for frame in page.frames:
        try:
            candidate = frame.locator(".yidun_voice__input").first
            if candidate.count() > 0 and candidate.is_visible(timeout=1000):
                voice_frame = frame
                voice_input = candidate
                break
        except Exception:
            continue
    if voice_input is None:
        raise RuntimeError("华为云语音验证码输入框在提交前不可见")
    runtime.human_fill_verified(page, voice_input, code)
    control = None
    for frame in (voice_frame, *page.frames):
        try:
            candidate = frame.locator(".yidun_control").first
            if candidate.count() > 0 and candidate.is_visible(timeout=800):
                control = candidate
                break
        except Exception:
            continue
    if control is None:
        raise RuntimeError("华为云语音验证码提交按钮在页面内不可见")
    if not runtime.human_click(control):
        raise RuntimeError("华为云语音验证码提交按钮点击失败")


def _wait_voice_result(page: Any, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    closed_since = 0.0
    while time.time() < deadline:
        _check_stop()
        body = _verification_body(page)
        body_lower = body.lower()
        if any(
            marker in body_lower
            for marker in (
                "verify failed",
                "verification failed",
                "incorrect verification",
                "refresh verify failed",
            )
        ):
            return False
        visible = _verification_popup_visible(page)
        if not visible:
            if not closed_since:
                closed_since = time.time()
            elif time.time() - closed_since >= 2.0:
                return True
        else:
            closed_since = 0.0
        snapshot = _page_snapshot(page)
        page_text = f"{body}\n{snapshot.get('body') or ''}".lower()
        if any(
            marker in page_text
            for marker in ("resend", "seconds", "verification code sent", "code sent")
        ):
            return True
        time.sleep(0.4)
    return False


def _wait_for_verification(
    page: Any,
    timeout: float,
    audio_queue: queue.Queue,
    workdir: Path,
) -> None:
    deadline = time.time() + timeout
    appeared = False
    max_attempts = max(1, int(getattr(cfg, "CLOAK_VOICE_CAPTCHA_RETRIES", 3) or 3))
    while time.time() < deadline:
        _check_stop()
        visible = _verification_popup_visible(page)
        if visible and not appeared:
            appeared = True
            _log(f"华为云验证弹窗已出现，开始语音识别（最多 {max_attempts} 次）")
            ready_deadline = time.time() + 60.0
            last_controls = ""
            while time.time() < ready_deadline:
                if _voice_challenge_controls_ready(page):
                    break
                _open_voice_mode(page)
                current_controls = " | ".join(_voice_challenge_debug(page, limit=6))
                if current_controls != last_controls:
                    last_controls = current_controls
                    _log(f"华为云验证组件仍在加载，controls={current_controls}")
                time.sleep(0.6)
            else:
                _log(
                    "华为云验证组件等待较久，继续尝试音频捕获，"
                    f"controls={_voice_challenge_debug(page)}"
                )

            consumed_sequence = 0
            last_error = ""
            for attempt in range(1, max_attempts + 1):
                _check_stop()
                if not _verification_popup_visible(page):
                    _log("华为云验证弹窗已关闭，停止语音重试")
                    return
                try:
                    code, consumed_sequence = _recognize_current_voice(
                        page,
                        audio_queue,
                        after_sequence=consumed_sequence,
                        workdir=workdir,
                    )
                    _submit_voice_code(page, code)
                    if _wait_voice_result(page):
                        _log(f"华为云语音验证通过（第 {attempt} 次）")
                        break
                    last_error = "验证码未通过"
                except Exception as exc:
                    last_error = str(exc)
                    _log(f"华为云语音验证第 {attempt} 次失败: {last_error}")
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"华为云语音验证连续 {max_attempts} 次失败: {last_error}"
                    )

                reopened = False
                try:
                    refresh = page.locator(".yidun_voice__refresh").first
                    if refresh.count() > 0 and refresh.is_visible(timeout=1000):
                        reopened = runtime.human_click(refresh)
                except Exception:
                    reopened = False
                if reopened:
                    _human_pause(0.8, 1.4)
                    _wait_for_voice_panel(page)
                    continue

                reopened = _play_voice_challenge(page)
                if reopened:
                    _human_pause(0.8, 1.4)
                    continue
                if not reopened:
                    try:
                        runtime.human_click(page.locator(".yidun_modal__close").first)
                    except Exception:
                        pass
                    _human_pause(0.4, 0.8)
                    if not _click_text_button(page, "Get Code"):
                        raise RuntimeError("语音验证失败且无法重新打开验证")
                _human_pause(0.8, 1.4)

        if appeared and not visible:
            _log("华为云验证弹窗已完成")
            return
        time.sleep(0.7)
    raise RuntimeError("等待华为云验证弹窗超时")


def _fill_email(page: Any, email: str) -> None:
    email_input = _placeholder_locator(page, "Email")
    try:
        if not email_input.is_visible(timeout=1500):
            email_input = page.locator(
                "input[placeholder*='email' i],input[type='email'],"
                "input[autocomplete='email']"
            ).first
    except Exception:
        pass
    runtime.human_fill(page, email_input, email)
    runtime.human_press(page, "Tab")
    deadline = time.time() + 8.0
    actual = ""
    while time.time() < deadline:
        _check_stop()
        actual = str(
            _safe_evaluate(
                page,
                r"""() => {
                  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                  const input = [...document.querySelectorAll('input')].find(el =>
                    visible(el) && String(el.placeholder || '').trim().toLowerCase() === 'email'
                  );
                  return input ? String(input.value || '') : '';
                }""",
            )
            or ""
        )
        if actual == str(email):
            break
        time.sleep(0.25)
    if actual != str(email):
        raise RuntimeError(
            "华为云邮箱输入未生效，"
            f"期望长度={len(str(email))}，实际长度={len(actual)}"
        )
    _human_pause(0.5, 1.0)


def _fill_email_code(page: Any, code: str) -> None:
    email_code_input = page.locator(
        "input[placeholder='Email code']"
    ).first
    runtime.human_fill(page, email_code_input, code)
    runtime.human_press(page, "Tab")
    deadline = time.time() + 8.0
    actual = ""
    while time.time() < deadline:
        _check_stop()
        actual = str(
            _safe_evaluate(
                page,
                r"""() => {
                  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                  const input = [...document.querySelectorAll('input')].find(el =>
                    visible(el) && String(el.placeholder || '').trim() === 'Email code'
                  );
                  return input ? String(input.value || '') : '';
                }""",
            )
            or ""
        )
        if actual == str(code):
            break
        time.sleep(0.25)
    if actual != str(code):
        raise RuntimeError(
            "华为云邮箱验证码输入未生效，"
            f"期望长度={len(str(code))}，实际长度={len(actual)}"
        )
    _human_pause(0.3, 0.7)


def _get_code_button(page: Any) -> Any:
    return page.get_by_role(
        "button",
        name=re.compile("Get Code", re.IGNORECASE),
    ).first


def _wait_get_code_enabled(page: Any, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        _check_stop()
        try:
            if _get_code_button(page).is_enabled(timeout=800):
                return True
        except Exception:
            pass
        time.sleep(0.35)
    return False


def _fill_password(page: Any, password: str) -> None:
    snapshot = _page_snapshot(page)
    password_count = sum(
        1
        for item in snapshot.get("controls") or []
        if item.get("tag") == "input" and item.get("type") == "password"
    )
    if password_count < 2:
        raise RuntimeError("华为云注册页未找到密码和确认密码输入框")
    runtime.human_fill(
        page,
        _placeholder_locator(page, "Password"),
        password,
    )
    runtime.human_press(page, "Tab")
    _human_pause(0.4, 0.8)
    runtime.human_fill(
        page,
        _placeholder_locator(page, "Confirm password"),
        password,
    )
    runtime.human_press(page, "Tab")
    deadline = time.time() + 8.0
    values: dict[str, str] = {}
    while time.time() < deadline:
        _check_stop()
        values = dict(
            _safe_evaluate(
                page,
                r"""() => {
                  const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
                    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
                  const result = {};
                  for (const el of [...document.querySelectorAll('input[type="password"]')]
                    .filter(visible)
                  ) {
                    const placeholder = String(el.placeholder || '').trim();
                    if (placeholder === 'Password' || placeholder === 'Confirm password') {
                      result[placeholder] = String(el.value || '');
                    }
                  }
                  return result;
                }""",
            )
            or {}
        )
        if values.get("Password") == str(password) and values.get(
            "Confirm password"
        ) == str(password):
            break
        time.sleep(0.25)
    if values.get("Password") != str(password) or values.get(
        "Confirm password"
    ) != str(password):
        raise RuntimeError(
            "华为云密码输入未生效，"
            f"Password={'set' if values.get('Password') else 'empty'}, "
            f"Confirm password="
            f"{'set' if values.get('Confirm password') else 'empty'}"
        )
    _log("华为云密码与确认密码已填写")


def _agree_terms(page: Any) -> None:
    locator = page.get_by_text(
        re.compile(
            "I have read and agree to the Huawei Cloud Customer Agreement",
            re.IGNORECASE,
        )
    ).first
    try:
        if runtime.human_click(locator):
            return
    except Exception:
        pass
    checkbox = page.locator("input[type='checkbox']").first
    if not runtime.human_click(checkbox):
        raise RuntimeError("华为云服务协议勾选失败")


def _submit_signup(page: Any) -> None:
    if not _click_text_button(page, "Sign Up"):
        raise RuntimeError("华为云 Sign Up 按钮不可用")


def _is_success_url(url: str) -> bool:
    value = str(url or "").lower()
    return HUAWEI_SUCCESS_HOST in value and HUAWEI_SUCCESS_MARKER in value


def _wait_signup_result(page: Any, timeout: float) -> str:
    deadline = time.time() + max(10.0, timeout)
    while time.time() < deadline:
        _check_stop()
        try:
            page.wait_for_load_state("domcontentloaded", timeout=1200)
        except Exception:
            pass
        snapshot = _page_snapshot(page)
        url = str(snapshot.get("url") or _page_url(page))
        if _is_success_url(url):
            return url
        body = _text(snapshot.get("body"), 3000)
        errors = [
            _text(item, 500)
            for item in snapshot.get("errors") or []
            if _text(item, 500)
        ]
        combined = " | ".join([body, *errors])
        lowered = combined.lower()
        if any(marker in lowered for marker in HUAWEI_ERROR_MARKERS):
            raise HuaweiAccountRejected(f"华为云账号创建失败: {combined}")
        time.sleep(0.8)
    raise RuntimeError("华为云注册提交后未进入账号中心完成页")


def _run_sync(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    run_ctx = run_ctx if run_ctx is not None else {}
    email = ""
    password = _generate_password()
    playwright = browser = context = page = None
    keep_browser = False
    try:
        _check_stop()
        configured_locale = str(getattr(cfg, "CLOAK_LOCALE", "") or "").strip()
        playwright, browser, context, page = runtime.launch_browser(
            proxy,
            locale=configured_locale or "en-US",
            timezone_name=str(getattr(cfg, "CLOAK_TIMEZONE", "") or "").strip(),
            geoip=bool(getattr(cfg, "CLOAK_GEOIP", True)),
            mute_audio=True,
        )
        _log("正在创建华为云账号")
        page.goto(
            HUAWEI_START_URL,
            wait_until="domcontentloaded",
            timeout=runtime.navigation_timeout_ms(),
        )
        _human_pause(0.8, 1.4)
        runtime.maybe_accept_cookies(page)

        email, email_jwt = get_email_and_token(
            proxy,
            assigned_domain=assigned_domain,
            batch_id=batch_id,
            worker_index=worker_index,
        )
        if not email:
            raise RuntimeError("邮箱服务未分配邮箱")
        set_last_email(email)
        _fill_email(page, email)
        if not _wait_get_code_enabled(page):
            raise RuntimeError("华为云邮箱填写后 Get Code 按钮未启用")

        session_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        workdir = HUAWEI_AUDIO_ROOT / session_id
        workdir.mkdir(parents=True, exist_ok=True)
        audio_sequence = [0]
        audio_queue: queue.Queue = queue.Queue()
        audio_active = [True]

        def handle_challenge_audio(response: Any) -> None:
            _challenge_audio_response(
                response,
                audio_queue,
                workdir,
                audio_sequence,
                audio_active,
            )

        page.on("response", handle_challenge_audio)
        try:
            if not _click_text_button(page, "Get Code"):
                raise RuntimeError("华为云 Get Code 按钮点击失败")
            _human_pause(0.7, 1.3)
            _wait_for_verification(
                page,
                timeout=float(
                    max(30, getattr(cfg, "CLOAK_ACTION_TIMEOUT", 30)) * 3
                ),
                audio_queue=audio_queue,
                workdir=workdir,
            )
        finally:
            audio_active[0] = False
            try:
                page.remove_listener("response", handle_challenge_audio)
            except Exception:
                pass
            shutil.rmtree(workdir, ignore_errors=True)

        email_code = get_oai_code(
            email,
            jwt=email_jwt,
            proxies=proxy,
            platform="huaweicloud",
            max_attempts=max(1, int(getattr(cfg, "MAX_OTP_RETRIES", 3) or 3)),
        )
        if not email_code:
            raise RuntimeError("华为云邮箱验证码获取失败")
        _fill_email_code(page, email_code)

        _fill_password(page, password)
        _agree_terms(page)
        _submit_signup(page)
        success_url = _wait_signup_result(
            page,
            timeout=float(max(30, getattr(cfg, "CLOAK_SESSION_TIMEOUT", 90))),
        )
        run_ctx["huaweicloud_registration_acknowledged"] = True
        _log(f"华为云注册完成: {mask_email(email)}")
        return {
            "email": email,
            "password": password,
            "url": success_url,
        }
    except Exception:
        keep_browser = bool(getattr(cfg, "CLOAK_KEEP_BROWSER_OPEN", False))
        raise
    finally:
        if keep_browser:
            _log("华为云注册失败，浏览器已保留")
        else:
            runtime.close_browser(playwright, browser, context)


def run(
    proxy: Optional[str],
    run_ctx: Optional[dict] = None,
    assigned_domain: Optional[str] = None,
    batch_id: Optional[int] = None,
    worker_index: Optional[int] = None,
) -> dict:
    return runtime.run_with_thread_affinity(
        _run_sync,
        thread_name="cloakbrowser-huaweicloud-register",
        proxy=proxy,
        run_ctx=run_ctx,
        assigned_domain=assigned_domain,
        batch_id=batch_id,
        worker_index=worker_index,
    )
