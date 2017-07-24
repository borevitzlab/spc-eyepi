import subprocess
import random, string, os, socket, json, time
from glob import glob
from urllib import request
import threading
import configparser
import yaml
import logging
import logging.config
import fcntl
import datetime
import collections
from dateutil import parser
import traceback

USBDEVFS_RESET = 21780
try:
    logging.config.fileConfig("logging.ini")
except:
    pass


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
directory = /picam
username = picam
password = DEFAULT_PASSWORD

[timelapse]
interval = 300
starttime = 00:00
stoptime = 23:59

[localfiles]
spooling_dir =
upload_dir =
"""

default_light_config = """

[light]
max_power = 1000
min_power = 0
wavelengths = "400nm,420nm,450nm,530nm,630nm,660nm,735nm"
csv_keys = "LED1,LED2,LED3,LED4,LED5,LED6,LED7"
file_path = "lights_byserial/{identifier}.scf"

[telnet]
telnet_host = "192.168.2.124"
telnet_port = 50630
set_all_command = setall {power}
set_wavelength_command = setwlrelpower {wavelength} {power}
set_all_wavelength_command = setwlsrelpower {} {} {} {} {} {} {}
get_wavelength_command = getwlrelpower {wavelength}

[url]
url_host = "192.168.2.124"
control_uri = /cgi-bin/userI.cgi
set_all_command = "setAllTo": {percent}, "setAllSub": "set"
set_all_wavelength_command = "wl1":{}, "wl2":{}, "wl3":{}, "wl4":{}, "wl5":{}, "wl6":{}, "wl7":{}

"""

default_chamber_config = """

# this file should live in "spc-eyepi/chambers_byip/<ip>.ini"
# this file shoudl be accompanied by a "spc-eyepi/light_configs_byip/<ip>" .csv or .slc file

[chamber]
name = GC36
temperature_multiplier = 10.0
file_path = "chambers_byip/{identifier}.scf"

[telnet]
telnet_host = 192.168.0.36
telnet_port = 50630
telnet_user = root
telnet_password = froot
deviceid = 0

# this should contain the ip address for a light, or no ip if  
[light] 
ip = 192.168.2.124

"""


def recursive_update(d, u):
    for k, v in u.items():
        if isinstance(v, collections.Mapping):
            r = recursive_update(d.get(k, {}), v)
            d[k] = r
        else:
            d[k] = u[k]
    return d


def get_generator(fh):
    while True:
        data = fh.readline()
        if not data:
            break
        yield data


class LazySolarCalcReader(object):
    def __init__(self, fn):
        self._fn = fn
        self._fh = open(self._fn)
        self._rewind()

    def _rewind(self, index=0):
        self._fh.seek(0)
        self._generator = get_generator(self._fh)
        self._index = 0
        while self._index < index:
            try:
                next(self._generator)
                self._index += 1
            except StopIteration:
                self._rewind(index=0)
                break

    def _getitem_slice(self, slice):
        start, stop, step = slice.start, slice.stop, slice.step
        start = 0 if start is None else start
        stop = float("inf") if stop is None else stop
        step = 1 if step is None else step
        if stop is not None and stop < start:
            start, stop = stop, start
            step *= -1
        r = []
        ii = start
        while ii <= stop:
            try:
                r.append(self[ii])
                ii += step
            except IndexError:
                break
        return r

    def _parse_line(self, line_str: str) -> list:
        """
        parses a string into a list that can be read by the chamber
        
        :param line_str:  
        :return: list of values.
        """
        line = line_str.strip().split(",")

        def f(v):
            try:
                return float(v)
            except:
                return v

        if len(line) in (16, 13):
            try:
                return [
                    parser.parse("{} {}".format(line[0], line[1]), dayfirst=True),
                    *map(f, line[2:-1]),
                    parser.parse(line[-1])
                ]
            except Exception as e:
                traceback.print_exc()
        else:
            try:
                return [
                    parser.parse(line[0], dayfirst=True),
                    *map(f, line[1:-1]),
                    parser.parse(line[-1])
                ]

            except:
                traceback.print_exc()
        return list(map(f, line))

    def __len__(self):
        tempindex = 0
        self._rewind()
        while True:
            try:
                next(self._generator)
                tempindex += 1
            except StopIteration:
                break
        self._rewind()
        return tempindex

    def _getitem_int(self, index):
        if index < 0:
            index = len(self)+index
            if index < 0:
                raise IndexError
        if index == 0:
            self._rewind()
        if index < self._index:
            self._rewind()
        v = ""
        while self._index <= index:
            try:
                v = next(self._generator)
                self._index += 1
            except StopIteration:
                raise IndexError
        return self._parse_line(v)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._getitem_slice(index)
        elif isinstance(index, int):
            return self._getitem_int(index)

    def __next__(self):
        v = self._parse_line(next(self._generator))
        self._index += 1
        return v

    def __iter__(self):
        self._rewind()
        for v in self._generator:
            yield self._parse_line(v)

    def __del__(self):
        self._fh.close()


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
    _watches = list()
    thread = None
    stop = False
    logger = logging.getLogger("SysUtil")

    def __init__(self):
        if SysUtil.thread is None:
            SysUtil.thread = threading.Thread(target=self._thread)
            SysUtil.thread.start()
        pass

    @staticmethod
    def write_global_config(data: dict):
        """
        Writes a global configuration to the global_config.yml 
        
        :param data: dict of data to write to the config
        :return: 
        """
        path = "/home/spc-eyepi/{}.yml".format(SysUtil.get_hostname())
        with open(path, 'r') as fh:
            current_config = yaml.load(fh.read())
        current_config = recursive_update(current_config, data)
        with open(path, 'w') as fh:
            yaml.dump(current_config, fh, default_flow_style=False)

    @staticmethod
    def reset_usb_device(bus: int, dev: int) -> bool:
        """
        resets a usb device.

        :param bus: bus number
        :type bus: int
        :param dev: device number of the device on the bus above
        :type dev: int
        """
        try:
            fn = "/dev/bus/usb/{bus:03d}/{dev:03d}".format(bus=bus, dev=dev)
            with open(fn, 'w', os.O_WRONLY) as f:
                fcntl.ioctl(f, USBDEVFS_RESET, 0)
            return True
        except Exception as e:
            SysUtil.logger.error("Couldnt reset usb device (possible filenotfound): {}".format(str(e)))

    @staticmethod
    def default_identifier(prefix=None):
        """
        returns an identifier, If no prefix available, generates something.

        :param prefix:
        :return: string of the itentifier.
        :rtype: str
        """
        if prefix:
            return SysUtil.get_identifier_from_name(prefix)
        else:
            from hashlib import md5
            serialnumber = ("AUTO_" + md5(bytes(prefix, 'utf-8')).hexdigest()[len("AUTO_"):])[:32]
            SysUtil.logger.warning("using autogenerated serialnumber {}".format(serialnumber))
            return serialnumber

    @staticmethod
    def _nested_lookup(key, document):
        """
        nested document lookup,
        works on dicts and lists

        :param key: string of key to lookup
        :param document: dict or list to lookup
        :return: yields item
        """
        if isinstance(document, list):
            for d in document:
                for result in SysUtil._nested_lookup(key, d):
                    yield result

        if isinstance(document, dict):
            for k, v in document.items():
                if k == key:
                    yield v
                elif isinstance(v, dict):
                    for result in SysUtil._nested_lookup(key, v):
                        yield result
                elif isinstance(v, list):
                    for d in v:
                        for result in SysUtil._nested_lookup(key, d):
                            yield result

    @staticmethod
    def sizeof_fmt(num, suffix='B')->str:
        """
        formats a number of bytes in to a human readable string.
        returns in SI units
        eg sizeof_fmt(1234) returns '1.2KiB'

        :param num: number of bytes to format
        :param suffix: the suffix to use
        :return: human formattted string.
        :rtype: str
        """
        for unit in ['', 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi']:
            if abs(num) < 1024.0:
                return "%3.1f%s%s" % (num, unit, suffix)
            num /= 1024.0
        return "%.1f%s%s" % (num, 'Yi', suffix)

    @classmethod
    def update_from_git(cls):
        """
        updates spc-eyepi from git.

        """
        os.system("git fetch --all;git reset --hard origin/master")
        os.system("systemctl restart spc-eyepi_capture.service")

    @classmethod
    def get_hostname(cls)->str:
        """
        gets the current hostname.
        if there is no /etc/hostname file, sets the hostname randomly.

        :return: the current hostname or the hostname it was set to
        :rtype: str
        """
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
    def set_hostname(cls, hostname: str):
        """
        sets the machines hosname, in /etc/hosts and /etc/hostname

        :param hostname: the string of which to set the hostname to.
        """
        try:
            with open(os.path.join("/etc/", "hostname"), 'w') as f:
                f.write(hostname + "\n")

            with open(os.path.join("/etc/", "hosts"), 'w') as hosts_file:
                h_tmpl = "127.0.0.1\tlocalhost.localdomain localhost {hostname}\n"
                h_tmpl += "::1\tlocalhost.localdomain localhost {hostname}\n"
                hosts_file.write(h_tmpl.format(hostname=hostname))
        except Exception as e:
            cls.logger.error("Failed setting hostname for machine. {}".format(str(e)))

    @classmethod
    def get_machineid(cls)->str:
        """
        gets the machine id, or initialises the machine id if it doesnt exist.

        :return: string of the machine-id
        :rtype: str
        """
        if abs(cls._machine_id[-1] - time.time()) > 10:
            if not os.path.isfile("/etc/machine-id"):
                os.system("systemd-machine-id-setup")
            with open("/etc/machine-id") as f:
                cls._machine_id = f.read().strip(), time.time()
        return cls._machine_id[0]

    @classmethod
    def get_tor_host(cls)->tuple:
        """
        gets a tuple of the current tor host.

        :return: tuple of hostname(onion address), client key, client name
        :rtype: tuple[str, str, str]
        """
        if abs(cls._tor_host[-1] - time.time()) > 10:
            try:
                with open("/home/tor_private/hostname") as f:
                    onion_address = f.read().replace('\n', '')
                cls._tor_host = onion_address.split(" ")[:3], time.time()
            except:
                cls._tor_host = ("unknown", 'unknown', "unknown"), time.time()
        return cls._tor_host[0]

    @classmethod
    def get_fs_space(cls)->tuple:
        """
        returns free/total space of root filesystem as bytes(?)

        :return: tuple of free/total space
        :rtype: tuple[int, int]
        """
        if abs(cls._fs[-1] - time.time()) > 10:
            try:
                a_statvfs = os.statvfs("/")
                cls._fs = (
                          a_statvfs.f_frsize * a_statvfs.f_bavail, a_statvfs.f_frsize * a_statvfs.f_blocks), time.time()
            except:
                cls._fs = (0, 0), time.time()
        return cls._fs[0]

    @classmethod
    def get_fs_space_mb(cls)->tuple:
        """
        returns the filesystems free space in mebibytes.
        see :func:`get_fs_space`

        :return: tuple of free/total space
        :rtype:tuple[int, int]
        """
        free_space, total_space = SysUtil.get_fs_space()
        for x in range(0, 2):
            free_space /= 1024.0
            total_space /= 1024.0
        return free_space, total_space

    @classmethod
    def get_version(cls)->str:
        """
        gets the "describe" version of the current git repo as a string.

        :return: the current version
        :rtype: str
        """
        if abs(cls._version[-1] - time.time()) > 10:
            try:

                cmd = "/usr/bin/git describe --always"
                cls._version = subprocess.check_output([cmd], shell=True).decode().strip("\n"), time.time()
            except:
                cls._version = "unknown", time.time()
        return cls._version[0]

    @classmethod
    def get_internal_ip(cls):
        """
        gets the internal ip by attempting to connect to googles DNS

        :return: the current internal ip
        :rtype: str
        """

        if abs(cls._ip_address[-1] - time.time()) > 10:
            try:
                try:
                    import netifaces
                    ip = netifaces.ifaddresses("tun0")[netifaces.AF_INET][0]["addr"]
                    cls._ip_address = ip, time.time()
                except:
                    import socket
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 0))
                    cls._ip_address = s.getsockname()[0], time.time()
            except:
                cls._ip_address = "0.0.0.0", time.time()
        return cls._ip_address[0]

    @classmethod
    def get_log_files(cls) -> list:
        """
        returns the spc-eyepi log files that have been rotated.

        :return: list of filenames
        :rtype: list(str)
        """
        return list(glob("/home/spc-eyepi/spc-eyepi.log.*"))

    @classmethod
    def clear_files(cls, filenames: list):
        """
        removes all files in the list provided, skipping and logging on an error removing
        todo: Do different things based on whether is a directory.

        :param filenames: list of directories or files
        :type filenames: list or tuple
        """
        for f in filenames:
            try:
                os.remove(f)
            except FileNotFoundError as e:
                cls.logger.debug(str(e))
            except IsADirectoryError as e:
                cls.logger.error(str(e))
            except Exception as e:
                cls.logger.error(str(e))

    @classmethod
    def get_isonow(cls):
        """
        gets the current time as an iso8601 string

        :return: the current time as iso8601
        :rtype: str
        """
        return datetime.datetime.now().isoformat()

    @classmethod
    def get_external_ip(cls):
        """
        returns the external IP address of the raspberry pi through api.ipify.org

        :return: the external ip address
        :rtype: str
        """
        if abs(cls._external_ip[-1] - time.time()) > 60:
            try:
                url = 'https://api.ipify.org/?format=json'
                response = request.urlopen(url, timeout=10).read().decode('utf-8')
                cls._external_ip = json.loads(response)['ip'], time.time()
            except:
                cls._external_ip = "0.0.0.0", time.time()
        return cls._external_ip[0]

    @classmethod
    def get_identifier_from_name(cls, name):
        """
        returns either the identifier (from name) or the name filled with the machine id
        clamps to 32 characters.

        :param name: name to fill
        :type name: str
        :return: filled name
        :rtype: str
        """
        identifier = "".join((x if idx > len(name) - 1 else name[idx] for idx, x in enumerate(cls.get_machineid())))
        return identifier[:32]

    @classmethod
    def get_identifier_from_filename(cls, file_name):
        """
        returns either the identifier (from the file name) or the name filled with the machine id

        :param file_name: filename
        :type file_name: str
        :return: string identifier,
        :rtype: str
        """
        fsn = next(iter(os.path.splitext(os.path.basename(file_name))), "")
        return cls.get_identifier_from_name(fsn)

    @classmethod
    def ensure_config(cls, identifier):
        """
        ensures a configuration file exists for this identifier.
        if a config file doesnt exist then it will create a default one.

        :param identifier: identifier to create or find a configuration file for.
        :type identifier: str
        :return: the configuration file dict or configparser object.
        :rtype: dict or configparser.ConfigParser
        """
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
            config['camera']['name'] = cls.get_hostname() + identifier[:6]

        cls.write_config(config, identifier)
        return config

    @classmethod
    def write_config(cls, config: configparser.ConfigParser, identifier: str, prefix="configs_byserial"):
        """
        writes a configuration file to an correct config file path.

        :param config: configuration file (configparser object)
        :type identifier: str
        :param identifier: identifier to user as the raget file name.
        :return: configparser object
        """
        path = SysUtil.identifier_to_ini(identifier, prefix=prefix)
        with open(path, 'w+') as configfile:
            config.write(configfile)
        return config

    @classmethod
    def identifier_to_ini(cls, identifier: str, prefix="configs_byserial")->str:
        """
        gets a valid .ini path for an identifier.

        :param identifier: identifier to find an ini for.
        :return: file path for identifier
        :rtype: str
        """
        for fn in glob("{prefix}/*.ini".format(prefix=prefix)):
            if identifier == cls.get_identifier_from_filename(fn):
                return fn
        else:
            return os.path.join("{prefix}/".format(prefix=prefix), identifier) + ".ini"


    @classmethod
    def load_or_fix_solarcalc(cls, fp: str)-> LazySolarCalcReader:
        """
        function to either load an existing fixed up solarcalc file or to coerce one into the fixed format.

        :param identifier: identifier of the light for which the solarcalc file exists.
        :type identifier: str
        :return: light timing data as a list of lists.
        :rtype: list(list())
        """
        lx = []

        path, ext = os.path.splitext(fp)
        if ext not in (".csv", ".slc"):
            raise ValueError("Only .csv or .slc files are supported")
        if not os.path.isfile(fp):
            SysUtil.logger.error("no SolarCalc file.")
            raise FileNotFoundError()
        return LazySolarCalcReader(fp)

        # headerstart = ['datetime', 'temp', 'relativehumidity']
        # headerend = ['total_solar_watt', 'simulated_datetime']
        # fill = "LED{}"
        #
        # if not os.path.isfile(fp):
        #     SysUtil.logger.error("no SolarCalc file.")
        #     raise FileNotFoundError()
        # if ext == ".slc":
        #     with open(fp) as f:
        #         lx = [x.strip().split(",") for x in f.readlines()]
        #         for i,l in enumerate(lx):
        #             lx[i][0] = parser.parse(lx[i][0])
        #             lx[i][-1] = parser.parse(lx[i][-1])
        # else:
        #     def get_lines(fh):
        #         with open(fh) as f:
        #             for line_str in f.readlines():
        #                 try:
        #                     line = line_str.strip().split(",")
        #                     l = [
        #                         parser.parse("{} {}".format(line[0], line[1])).isoformat(),
        #                         *line[2:-1],
        #                         parser.parse(line[-1]).isoformat()
        #                     ]
        #                     yield l
        #                 except Exception as e:
        #                     SysUtil.logger.error("Couldnt fix solarcalc file. {}".format(str(e)))
        #                     traceback.print_exc()
        #
        #     with open(path + ".slc", 'w') as slc:
        #         for line in get_lines(fp):
        #             slc.write(",".join(line)+"\n")
        #             lx.append(line)
        #
        #         # led_fields = [fill.format(i) for i in range(len(lx[0])-len(headerstart)-len(headerend))]
        #         # header = headerstart + led_fields + headerend
        #         # lx.insert(0, header)
        # return lx[1:]

    @classmethod
    def identifier_to_yml(cls, identifier: str)->str:
        """
        the same as identifier_to_ini but for yml files

        :param identifier: identifier for a matching yml file.
        :type identifier: str
        :return: string filepath for the yml file.
        :rtype: str
        """
        for fn in glob("configs_byserial/*.yml"):
            if identifier == cls.get_identifier_from_filename(fn):
                return fn
        else:
            return os.path.join("configs_byserial/", identifier) + ".yml"

    @classmethod
    def configs_from_identifiers(cls, identifiers: set) -> dict:
        """
        given a set of identifiers, returns a dictionary of the data contained in those config files with the key
        for each config file data being the identifier

        :param identifiers:
        :type identifiers: list(str)
        :return: dictionary of configuration datas
        :rtype: dict(str: dict)
        """
        data = dict()
        for ini in ["configs_byserial/{}.ini".format(x) for x in identifiers]:
            cfg = configparser.ConfigParser()
            cfg.read(ini)
            d = dict()
            d = {section: dict(cfg.items(section)) for section in cfg.sections()}
            data[cls.get_identifier_from_filename(ini)] = d
        return data

    @classmethod
    def add_watch(cls, path: str, callback):
        """
        adds a watch that calls the callback on file change

        :param path: path of the file to watch
        :type path: str
        :param callback: function signature to call when the file is changed
        """
        cls._watches.append((path, os.stat(path).st_mtime, callback))

    @classmethod
    def open_yaml(cls, filename):
        """
        opens a yaml file using yaml.load

        :param filename: yaml file to load
        :return: dictionary of values in yaml file
        :rtype: dict
        """
        try:
            with open(filename) as e:
                q = yaml.load(e.read())
            return q
        except Exception as e:
            print(str(e))
            return dict()

    @classmethod
    def _thread(cls):
        """
        runs the watchers
        """
        while True and not cls.stop:
            try:
                for index, (path, mtime, callback) in enumerate(cls._watches):
                    tmt = os.stat(path).st_mtime
                    if tmt != mtime:
                        cls._watches[index] = (path, tmt, callback)
                        try:
                            print("calling {}".format(callback))
                            callback()
                        except Exception as e:
                            print(str(e))
                time.sleep(1)
            except Exception as e:
                break
        cls.thread = None