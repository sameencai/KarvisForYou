# -*- coding: utf-8 -*-
"""
企业微信消息加解密
从 wechat-sync 提取，支持 pycryptodome 和 cryptography 两种后端。
"""
import base64
import hashlib
import struct
import sys


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


class WXBizMsgCrypt:
    """企业微信消息加解密"""

    def __init__(self, token, encoding_aes_key, corp_id):
        self.token = token
        self.corp_id = corp_id
        try:
            self.aes_key = base64.b64decode(encoding_aes_key + "=")
        except Exception as e:
            _log(f"AES Key 解码失败: {e}")
            self.aes_key = None

    def _get_sha1(self, *args):
        sort_list = sorted([str(arg) for arg in args])
        return hashlib.sha1(''.join(sort_list).encode('utf-8')).hexdigest()

    def _pkcs7_decode(self, text):
        pad = text[-1]
        pad_len = pad if isinstance(pad, int) else ord(pad)
        return text[:-pad_len]

    def _decrypt(self, encrypted):
        """解密，自动选择可用的加密库"""
        try:
            from Crypto.Cipher import AES
            cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
            decrypted = cipher.decrypt(base64.b64decode(encrypted))
        except ImportError:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
            cipher = Cipher(algorithms.AES(self.aes_key),
                            modes.CBC(self.aes_key[:16]),
                            backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()

        decrypted = self._pkcs7_decode(decrypted)
        msg_len = struct.unpack('>I', decrypted[16:20])[0]
        return decrypted[20:20 + msg_len].decode('utf-8')

    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        """验证 URL，返回解密后的 echostr"""
        signature = self._get_sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            _log(f"[验证] 签名不匹配: 计算={signature}, 期望={msg_signature}")
            return None
        try:
            return self._decrypt(echostr)
        except Exception as e:
            _log(f"[验证] 解密失败: {e}")
            return None

    def decrypt_msg(self, msg_signature, timestamp, nonce, encrypted_msg):
        """解密消息"""
        signature = self._get_sha1(self.token, timestamp, nonce, encrypted_msg)
        if signature != msg_signature:
            _log(f"[解密] 签名不匹配")
            return None
        try:
            msg = self._decrypt(encrypted_msg)
            _log(f"[解密] 成功，长度: {len(msg)}")
            return msg
        except Exception as e:
            _log(f"[解密] 失败: {e}")
            return None
