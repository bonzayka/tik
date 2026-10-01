import io
import json
import logging
import os
import platform
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("vless_tunnel")

class VlessTunnelManager:
    """
    Manages a local Xray tunnel to convert VLESS Reality links into
    a local HTTP/SOCKS5 proxy (e.g., http://127.0.0.1:10809).
    Requires zero root permissions and runs on Linux and Windows.
    """
    def __init__(self, vless_url: str = "", base_dir: Optional[Path] = None, port: int = 10809):
        self.vless_url = (vless_url or "").strip()
        self.base_dir = base_dir or Path(__file__).resolve().parent
        self.bin_dir = self.base_dir / "bin"
        self.port = port
        self.proc: Optional[subprocess.Popen] = None
        self.is_running = False

    def get_proxy_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def parse_vless(self) -> Dict[str, Any]:
        """Parses a vless:// URL into an Xray client configuration dictionary."""
        parsed = urlparse(self.vless_url)
        user_id = parsed.username
        host = parsed.hostname
        port = parsed.port or 443
        qs = parse_qs(parsed.query)

        flow = qs.get('flow', [''])[0]
        pbk = qs.get('pbk', [''])[0]
        sid = qs.get('sid', [''])[0]
        sni = qs.get('sni', [host])[0]
        fp = qs.get('fp', ['firefox'])[0]
        spx = qs.get('spx', ['/'])[0]
        net_type = qs.get('type', ['tcp'])[0]
        security = qs.get('security', ['reality'])[0]

        return {
            'log': {'loglevel': 'warning'},
            'inbounds': [
                {
                    'port': self.port,
                    'listen': '127.0.0.1',
                    'protocol': 'http',
                    'settings': {}
                },
                {
                    'port': self.port - 1,
                    'listen': '127.0.0.1',
                    'protocol': 'socks',
                    'settings': {'auth': 'noauth', 'udp': True}
                }
            ],
            'outbounds': [
                {
                    'protocol': 'vless',
                    'settings': {
                        'vnext': [
                            {
                                'address': host,
                                'port': port,
                                'users': [
                                    {
                                        'id': user_id,
                                        'encryption': 'none',
                                        'flow': flow
                                    }
                                ]
                            }
                        ]
                    },
                    'streamSettings': {
                        'network': net_type,
                        'security': security,
                        'realitySettings': {
                            'show': False,
                            'fingerprint': fp,
                            'serverName': sni,
                            'publicKey': pbk,
                            'shortId': sid,
                            'spiderX': spx
                        }
                    }
                }
            ]
        }

    def ensure_xray_binary(self) -> Path:
        """Downloads standalone Xray-core binary if not already present."""
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        is_windows = sys.platform == "win32"
        xray_name = "xray.exe" if is_windows else "xray"
        xray_path = self.bin_dir / xray_name

        if xray_path.exists():
            return xray_path

        machine = platform.machine().lower()
        if is_windows:
            asset_name = "Xray-windows-64.zip"
        elif "arm" in machine or "aarch64" in machine:
            asset_name = "Xray-linux-arm64-v8a.zip"
        else:
            asset_name = "Xray-linux-64.zip"

        url = f"https://github.com/XTLS/Xray-core/releases/download/v26.3.27/{asset_name}"
        logger.info(f"Downloading Xray core for VLESS tunnel from {url}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extractall(self.bin_dir)

        if not is_windows and xray_path.exists():
            try:
                os.chmod(xray_path, 0o755)
            except Exception:
                pass

        logger.info(f"Xray core ready at: {xray_path}")
        return xray_path

    def start(self) -> bool:
        """Starts the local Xray background process."""
        if not self.vless_url or not self.vless_url.startswith("vless://"):
            return False

        try:
            xray_bin = self.ensure_xray_binary()
            config_data = self.parse_vless()
            config_file = self.bin_dir / "xray_vless_config.json"
            config_file.write_text(json.dumps(config_data, indent=2), encoding="utf-8")

            self.stop()

            logger.info(f"Starting local VLESS tunnel on port {self.port}...")
            self.proc = subprocess.Popen(
                [str(xray_bin), "run", "-c", str(config_file)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.5)

            if self.proc.poll() is None:
                self.is_running = True
                logger.info(f"✅ VLESS Reality туннель активен! Локальный прокси: {self.get_proxy_url()}")
                return True
            else:
                logger.error(f"❌ Xray завершился с кодом {self.proc.returncode}")
                return False
        except Exception as e:
            logger.error(f"Ошибка при запуске VLESS туннеля: {e}", exc_info=True)
            return False

    def stop(self):
        """Stops the local Xray background process."""
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        self.is_running = False
