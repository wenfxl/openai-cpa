import os
import json
import base64
import socket as _socket
import socks
import urllib.parse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from utils import config as cfg

class GmailOAuthHandler:
    SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
    _original_socket = None
    _current_proxy = None

    @staticmethod
    def _apply_socks_proxy(proxy_str):
        """在 socket 级别应用 socks5 代理"""
        if not proxy_str:
            return
        if GmailOAuthHandler._original_socket is None:
            GmailOAuthHandler._original_socket = _socket.socket
        GmailOAuthHandler._current_proxy = proxy_str
        parsed = urllib.parse.urlparse(proxy_str)
        if parsed.scheme in ('socks5', 'socks5h'):
            socks.set_default_proxy(
                socks.SOCKS5,
                parsed.hostname,
                parsed.port or 1080,
                username=parsed.username,
                password=parsed.password
            )
            _socket.socket = socks.socksocket

    @staticmethod
    def _restore_socket():
        """恢复原始 socket"""
        if GmailOAuthHandler._original_socket is not None:
            _socket.socket = GmailOAuthHandler._original_socket
            GmailOAuthHandler._original_socket = None
            GmailOAuthHandler._current_proxy = None

    @staticmethod
    def _set_proxy(proxy):
        if proxy:
            if isinstance(proxy, dict):
                proxy = proxy.get('https') or proxy.get('http')

            if proxy and isinstance(proxy, str):
                os.environ['http_proxy'] = proxy
                os.environ['https_proxy'] = proxy
                os.environ['no_proxy'] = 'localhost,127.0.0.1'

    @staticmethod
    def _clear_proxy():
        os.environ.pop('http_proxy', None)
        os.environ.pop('https_proxy', None)

    @staticmethod
    def get_authorization_url(client_secrets_path):
        flow = Flow.from_client_secrets_file(
            client_secrets_path,
            scopes=GmailOAuthHandler.SCOPES,
            redirect_uri='http://localhost'
        )
        auth_url, _ = flow.authorization_url(prompt='consent', access_type='offline')

        return auth_url, flow.code_verifier

    @staticmethod
    def save_token_from_code(client_secrets_path, code, token_save_path, code_verifier=None, proxy=None):
        GmailOAuthHandler._set_proxy(proxy)
        try:
            flow = Flow.from_client_secrets_file(
                client_secrets_path,
                scopes=GmailOAuthHandler.SCOPES,
                redirect_uri='http://localhost'
            )
            flow.fetch_token(code=code, code_verifier=code_verifier)

            creds = flow.credentials
            with open(token_save_path, 'w') as f:
                f.write(creds.to_json())
            return True, "授权成功"
        except Exception as e:
            return False, f"授权失败: {str(e)}"
        finally:
            GmailOAuthHandler._clear_proxy()

    @staticmethod
    def get_service(client_secrets_path, token_path, proxy=None):
        if not os.path.exists(token_path):
            return None

        try:
            creds = Credentials.from_authorized_user_file(token_path, GmailOAuthHandler.SCOPES)

            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, 'w') as f:
                    f.write(creds.to_json())

            GmailOAuthHandler._apply_socks_proxy(proxy)
            return build('gmail', 'v1', credentials=creds, static_discovery=False)

        except Exception as e:
            from utils import config as cfg
            print(f"[{cfg.ts()}] [ERROR] Gmail 服务启动失败: {e}")
            GmailOAuthHandler._restore_socket()
            return None

    @staticmethod
    def fetch_and_mark_read(service, target_email, search_query="is:unread"):
        if not service: return []
        try:

            results = service.users().messages().list(userId='me', q=search_query).execute()
            messages = results.get('messages', [])
            extracted_data = []
            for msg_info in messages:
                msg = service.users().messages().get(userId='me', id=msg_info['id']).execute()

                # --- 详细解析 Header ---
                headers = msg.get('payload', {}).get('headers', [])
                subject = ""
                to_addr = ""
                delivered_to = ""

                for h in headers:
                    name = h['name'].lower()
                    if name == 'subject':
                        subject = h['value']
                    elif name == 'to':
                        to_addr = h['value'].lower()
                    elif name == 'delivered-to':
                        delivered_to = h['value'].lower()

                is_match = (target_email.lower() in to_addr) or (target_email.lower() in delivered_to)
                if not is_match:
                    continue
                payload = msg.get('payload', {})
                body = ""

                def get_body_data(parts):
                    for part in parts:
                        if part['mimeType'] == 'text/plain': return part['body'].get('data', '')
                        if 'parts' in part:
                            res = get_body_data(part['parts'])
                            if res: return res
                    return ""

                if 'parts' in payload:
                    body_raw = get_body_data(payload['parts'])
                else:
                    body_raw = payload.get('body', {}).get('data', '')

                if body_raw:
                    body = base64.urlsafe_b64decode(body_raw).decode('utf-8', 'ignore')
                service.users().messages().batchModify(
                    userId='me',
                    body={'ids': [msg_info['id']], 'removeLabelIds': ['UNREAD']}
                ).execute()

                extracted_data.append({'subject': subject, 'body': body})

            return extracted_data
        except Exception as e:
            print(f"[{cfg.ts()}] [SKIP] DEBUG: 邮件解析过程报错: {e}")
            return []
        finally:
            GmailOAuthHandler._restore_socket()
