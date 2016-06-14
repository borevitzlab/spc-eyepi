__author__ = 'Gareth Dunstone'
import io
import os
import socket
import ssl
import struct
import textwrap
from base64 import b64encode
from urllib import request, parse
import paramiko
from cryptography import utils
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from .SysUtil import SysUtil

keyserver = "https://traitcapture.org"


def serialize_signature(signature) -> str:
    """
    formats the signature for the server, with the correct boundaries
    :param signature: raw bytes signature.
    :return: str formatted signature for sending to the server.
    """
    signature = b64encode(signature).decode("utf-8")
    signature = textwrap.fill(signature, 43)
    return "\n-----BEGIN SIGNATURE-----\n{}\n-----END SIGNATURE-----\n".format(signature)


def ssh_public_key(keypair: rsa.RSAPrivateKeyWithSerialization) -> str:
    """
    converts an rsa keypair to openssh format public key
    :param keypair:
    :return: string of public key
    """
    eb = utils.int_to_bytes(keypair.public_key().public_numbers().e)
    nb = utils.int_to_bytes(keypair.public_key().public_numbers().n)
    if eb[0] & 0x80: eb = bytes([0x00]) + eb
    if nb[0] & 0x80: nb = bytes([0x00]) + nb
    keyparts = [b'ssh-rsa', eb, nb]
    keystring = b''.join([struct.pack(">I", len(kp)) + kp for kp in keyparts])
    return str(b'ssh-rsa ' + b64encode(keystring), encoding='utf-8')


class SSHManager(object):
    def __init__(self, path="/home/.ssh"):
        self._key = self.ssh_agentKey = None
        self.path = path

        self.token_path = os.path.join(path, "key_token")
        self.priv_path = os.path.join(path, "id_rsa")
        self.pub_path = os.path.join(path, "id_rsa.pub")
        self.authorized_keys_path = os.path.join(path, "authorized_keys")
        if os.path.isfile(self.token_path):
            with open(self.token_path, 'r') as key_token_file:
                token = key_token_file.read().strip()
            self.get_new_key_from_server(token)
            os.remove(self.token_path)

        if os.path.isfile(self.priv_path) and not self.ssh_key:
            try:
                with open(self.priv_path, 'rb') as f:
                    self.key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
            except Exception as e:
                print(str(e))
                self.key = None

    @property
    def paramiko_key(self):
        return self.ssh_agentKey

    @property
    def ssh_key(self):
        return self._key

    @ssh_key.setter
    def ssh_key(self, value):
        self._key = serialization.load_pem_private_key(value, password=None, backend=default_backend())
        pbytes = self._key.private_bytes(encoding=serialization.Encoding.PEM,
                                                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                                                    encryption_algorithm=serialization.NoEncryption())
        key_io = io.StringIO(pbytes.decode("utf-8"))
        self.ssh_agentKey = paramiko.RSAKey.from_private_key(key_io)

    def get_new_key_from_server(self, token):
        """
        acquires an ssh key from the server with a token.
        :param token: a string token to send to the server.
        :return:
        """

        req = request.Request(keyserver+'api/camera/id_rsa/{}/{}/{}'.format(token,
                                                                            SysUtil.get_machineid(),
                                                                            SysUtil.get_hostname()))
        handler = request.HTTPSHandler(context=ssl.SSLContext(ssl.PROTOCOL_TLSv1_2))
        opener = request.build_opener(handler)
        data = opener.open(req)
        d = data.read()
        self.ssh_key = d
        self.write_key_to_path()

    def write_key_to_path(self):
        """
        writes public and private keys to their respective paths
        :return:
        """
        priv_bytes = self.ssh_key.private_bytes(encoding=serialization.Encoding.PEM,
                                                format=serialization.PrivateFormat.TraditionalOpenSSL,
                                                encryption_algorithm=serialization.NoEncryption())
        with open(self.priv_path, 'wb') as id_rsa:
            id_rsa.write(priv_bytes)

        os.chmod(self.priv_path, 0o600)

        ssh_key_string = self.public_ssh_key_string
        with open(self.pub_path, 'w') as id_rsa_pub:
            id_rsa_pub.write(ssh_key_string)
        os.chmod(self.pub_path, 0o644)

        with open(self.authorized_keys_path, 'w') as authorized_keys:
            authorized_keys.write(ssh_key_string)
        os.chmod(self.authorized_keys_path, 0o744)

    @property
    def public_ssh_key_string(self) -> str:
        """
        gets the public ssh key string.
        :return:
        """
        if self.ssh_key:
            return ssh_public_key(self.ssh_key)
        return str()

    def sign_message(self, message) -> str:
        """
        signs a text message.
        :param message: utf-8 encoded string
        :return str: formatted string with message\nsignature
        """
        if not self.ssh_key:
            return message
        signer = self.ssh_key.signer(padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH
        ),
            hashes.SHA256())
        signer.update(bytes(message, "utf-8"))
        return serialize_signature(signer.finalize())
