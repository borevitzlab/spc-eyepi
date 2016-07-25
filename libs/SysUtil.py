import subprocess
import random, string, os, socket, json, time
from glob import glob
from urllib import request
import threading
import configparser


def sizeof_fmt(num, suffix='B'):
    for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
        if abs(num) < 1024.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1024.0
    return "%.1f%s%s" % (num, 'Yi', suffix)

default_config = """
[DEFAULT]
exposure = 0
enabled = on
resize = on

[camera]
name =
enabled = on

[ftp]
enabled = on
replace = on
resize = on
timestamped = on
server = sftp.traitcapture.org
directory = /
username = DEFAULT_USER
password = DEFAULT_PASSWORD

[timelapse]
interval = 300
starttime = 00:00
stoptime = 23:59

[localfiles]
spooling_dir =
upload_dir =
"""


class SysUtil(object):
    """
    System utility class.
    Helper class to cache various things like the hostname, machine-id, amount of space in the filesystem.
    """
    _ip_address = "0.0.0.0", 0
    _external_ip = "0.0.0.0", 0
    _machine_id = "", 0
    _hostname = "HOSTNAME", 0
    _tor_host = ("unknown.onion", "not a real key", "not a real client"), 0
    _version = "Unknown spc-eyepi version", 0
    a_statvfs = os.statvfs("/")
    _fs = (a_statvfs.f_frsize * a_statvfs.f_bavail, a_statvfs.f_frsize * a_statvfs.f_blocks), 0
    _watches = dict()
    thread = None
    stop = False

    def __init__(self):
        if SysUtil.thread is None:
            SysUtil.thread = threading.Thread(target=self._thread)
            SysUtil.thread.start()
        pass

    @staticmethod
    def sizeof_fmt(num, suffix='B'):
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)

    @classmethod
    def get_hostname(cls):
        if abs(cls._hostname[-1] - time.time()) > 10:
            if not os.path.isfile("/etc/hostname"):
                hostname = "".join(random.choice(string.ascii_letters) for _ in range(8))
                os.system("hostname {}".format(cls._hostname))
            else:
                with open("/etc/hostname", "r") as fn:
                    hostname = fn.read().strip()
            cls._hostname = hostname, time.time()
        return cls._hostname[0]

    @classmethod
    def get_machineid(cls):
        if abs(cls._machine_id[-1] - time.time()) > 10:
            if not os.path.isfile("/etc/machine-id"):
                os.system("systemd-machine-id-setup")
            with open("/etc/machine-id") as f:
                cls._machine_id = f.read().strip(), time.time()
        return cls._machine_id[0]

    @classmethod
    def get_tor_host(cls):
        if abs(cls._tor_host[-1] - time.time()) > 10:
            try:
                with open("/home/tor_private/hostname") as f:
                    onion_address = f.read().replace('\n', '')
                cls._tor_host = onion_address.split(" ")[:3], time.time()
            except:
                cls._tor_host = ("unknown", 'unknown', "unknown"), time.time()
        return cls._tor_host[0]

    @classmethod
    def get_fs_space(cls):
        """
        returns free/total
        :return:
        """
        if abs(cls._fs[-1] - time.time()) > 10:
            try:
                a_statvfs = os.statvfs("/")
                cls._fs = (a_statvfs.f_frsize * a_statvfs.f_bavail, a_statvfs.f_frsize * a_statvfs.f_blocks), time.time()
            except:
                cls._fs = (0, 0), time.time()
        return cls._fs[0]

    @classmethod
    def get_fs_space_mb(cls):
        free_space, total_space = SysUtil.get_fs_space()
        for x in range(0, 2):
            free_space /= 1024.0
            total_space /= 1024.0
        return free_space, total_space

    @classmethod
    def get_version(cls):
        if abs(cls._version[-1] - time.time()) > 10:
            try:
                cls._version = subprocess.check_output(["/usr/bin/git describe --always"], shell=True).decode().strip("\n"), time.time()
            except:
                cls._version = "unknown", time.time()
        return cls._version[0]

    @classmethod
    def get_internal_ip(cls):
        if abs(cls._ip_address[-1] - time.time()) > 10:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 0))
                cls._ip_address = s.getsockname()[0], time.time()
            except:
                cls._ip_address = "0.0.0.0", time.time()
        return cls._ip_address[0]

    @classmethod
    def get_external_ip(cls):
        """
        returns the external IP address of the raspberry pi.
        :return:
        """
        if abs(cls._external_ip[-1] - time.time()) > 60:
            try:
                url = 'https://api.ipify.org/?format=json'
                cls._external_ip = json.loads(request.urlopen(url,
                                                              timeout=10).read().decode('utf-8'))['ip'], time.time()
            except:
                cls._external_ip = "0.0.0.0", time.time()
        return cls._external_ip[0]


    @classmethod
    def get_identifier_from_name(cls, name):
        """
        returns either the identifier (from name) or the name filled with the machine id
        clamps to 32 characters.
        :param name: name to fill
        :return:
        """
        identifier = "".join((x if idx > len(name) - 1 else name[idx] for idx, x in enumerate(cls.get_machineid())))
        return identifier[:32]

    @classmethod
    def get_identifier_from_filename(cls, file_name):
        """
        returns either the identifier (from the file name) or the name filled with the machine id
        :param file_name: filename
        :return:
        """
        fsn = next(iter(os.path.splitext(os.path.basename(file_name))), "")
        return cls.get_identifier_from_name(fsn)

    @classmethod
    def ensure_config(cls, identifier):
        config = configparser.ConfigParser()
        config.read_string(default_config)
        path = cls.identifier_to_ini(identifier)
        try:
            if len(config.read(path)):
                return config
        except Exception as e:
            print(str(e))

        if not config['localfiles']['spooling_dir']:
            config['localfiles']['spooling_dir'] = "/home/images/spool/{}".format(identifier)

        if not config['localfiles']['upload_dir']:
            config['localfiles']['upload_dir'] = "/home/images/upload/{}".format(identifier)

        if not config['camera']['name']:
            config['camera']['name'] = cls.get_hostname()+identifier[:6]

        cls.write_config(config, identifier)
        return config

    @classmethod
    def write_config(cls, config, identifier):
        path = SysUtil.identifier_to_ini(identifier)
        with open(path, 'w+') as configfile:
            config.write(configfile)
        return config

    @classmethod
    def identifier_to_ini(cls, identifier):
        for fn in glob("configs_byserial/*.ini"):
            if identifier == cls.get_identifier_from_filename(fn):
                return fn
        else:
            return os.path.join("configs_byserial/", identifier) + ".ini"

    @classmethod
    def all_config_data(cls):
        data = dict()
        for ini in glob("configs_byserial/*.ini", recursive=True):
            cfg = configparser.ConfigParser()
            cfg.read(ini)
            d = dict()
            d = {section: dict(cfg.items(section)) for section in cfg.sections()}
            data[cls.get_identifier_from_filename(ini)] = d
        return data

    @classmethod
    def add_watch(cls, path, callback):
        cls._watches["path"] = os.stat(path).st_mtime, callback

    @classmethod
    def _thread(cls):
        while True and not cls.stop:
            try:
                for path, (mtime, callback) in cls._watches.items():
                    tmt = os.stat(path).st_mtime
                    if tmt != mtime:
                        cls._watches[path] = tmt, callback
                        try:
                            print("calling {}".format(callback))
                            callback()
                        except Exception as e:
                            print(str(e))
                time.sleep(1)
            except Exception as e:
                break
        cls.thread = None


class Test(object):
    class testCLS(object):
        def __init__(self):
            self.path = "test.tmp"
            self.completed_setup = False

        def setup(self):
            print("mock setup function called")
            self.completed_setup = True

    def __init__(self):
        self.passed = 0
        self.failed = []


    def test_caching(self, function, private):
        lasttime = private[-1]
        time.sleep(20)
        try:
            a = function()
            lasttime2 = private[-1]
        except Exception as e:
            self.failed.append(e)
        assert lasttime != lasttime2, "access times are the same what gives?"

    def test_system_id(self):
        a = SysUtil.get_machineid()
        try:
            while True:
                b = SysUtil.get_machineid()
                assert b == a, 'at some point the system id changed, this is wrong.'
                b = a
                lasttime2 = SysUtil._machine_id[-1]
        except Exception as e:
            self.failed.append(e)


    def test_watcher(self):
        c = Test.testCLS()
        try:
            file = open(c.path, 'w')
            file.write("test data")
            file.close()
            SysUtil().add_watch("test.tmp", c.setup)
            time.sleep(2)
            file = open(c.path, 'w')
            file.write("changed test data")
            file.close()
            time.sleep(2)
            os.remove(c.path)
            SysUtil.stop = True

        except Exception as e:
            self.failed.append(e)
        assert not c.completed_setup, "callback was not called from watcher"
        assert not SysUtil.thread, "thread not closed"
        assert not os.path.exists(c.path), 'test didnt remove file'
