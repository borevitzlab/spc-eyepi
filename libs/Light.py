import datetime
import operator
import logging.config
import time
from telnetlib import Telnet
import json
import requests
from collections import deque
from threading import Thread, Event
from libs.SysUtil import SysUtil

try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass


def clamp(v: float, minimum: float, maximum: float) -> float:
    """
    clamps a number to the minimum and maximum.
    :param v:
    :param minimum:
    :param maximum:
    :return:
    """
    return min(max(v, minimum), maximum)


class Controller(object):
    """
    controller abstract that takes a dictionary config section and sets self attributes to it.
    """

    def __init__(self, config_section: dict):
        self.min = 0
        self.max = 1000
        self.logger = logging.getLogger(str(self.__class__))
        self.get_wavelength_command = \
            self.set_wavelength_command = \
            self.set_all_command = \
            self.set_all_wavelength_command = ""
        for k, v in config_section.items():
            setattr(self, k, v)

    def _run_command(self, cmd):
        """
        unimplemented,
        override this to define how the controller should do things.
        
        :return:
        """
        return False

    def set_all(self, power: int = None, percent: int = None):
        """
        sets all wavelengths to either an absolute value or a percentage of the total
        
        :param power: power to set all wavelengths to
        :param percent: 0-100 percentage to set the lights to.
        :return: true/false depending on whether the command was successful
        """
        if not self.set_all_command:
            self.logger.error("set_all call without set_all_command")
            return None
        if percent:
            power = int(self.max * (percent / 100) + self.min)
        cmd = None
        if "{power}" in self.set_all_command:
            cmd = self.set_all_command.format(power=power)
        elif "{percent}" in self.set_all_command:
            cmd = self.set_all_command.format(percent=percent)
        else:
            self.logger.error("set_wavelength no cmd")
            return None
        return self._run_command(cmd)

    def set_wavelength(self, wl: str, power: int = None, percent: int = None):
        """
        sets a specific wavelength to a value
        either a power or a percent must be specified
        
        :param wl: string of wavelength name (eg 400nm)
        :param power: absolute power value
        :param percent: percent value, calculated from min/max.
        :return:
        """
        if not self.set_wavelength_command:
            self.logger.error("set_wavelength call without set_wavelength_command")
            return None
        if percent:
            power = int(self.max * (percent / 100) + self.min)
        cmd = None
        if "{power}" in self.set_wavelength_command:
            cmd = self.set_wavelength_command.format(wavelength=wl, power=power)
        elif "{percent}" in self.set_wavelength_command:
            cmd = self.set_wavelength_command.format(wavelength=wl, percent=percent)
        else:
            self.logger.error("set_wavelength no cmd")
            return None
        return self._run_command(cmd)

    def set_all_wavelengths(self, values: dict):
        """
        sets all wavelengths to specific values.
        only absolute values may be specified
        values should be specified as a dict of wavelength: value
        :param values: dict of wavelengths and their respective values
        :return:
        """

        if not self.set_all_wavelength_command:
            self.logger.error("set_all_wavelengths call without set_all_wavelength_command")
            return None

        sorted_values = sorted(values.items(), key=operator.itemgetter(0))
        if len(values) < self.set_all_wavelength_command.count("{}"):
            self.logger.error("Not enough wavelengths specified for set_all_wavelengths, padding with 0s")
            diff = self.set_all_wavelength_command.count("{}") - len(values)
            sorted_values.extend(("padded", 0) for _ in range(diff))

        cmd = self.set_all_wavelength_command.format(*(clamp(v[1], self.min, self.max) for v in sorted_values))
        return self._run_command(cmd)

    def get_wavelength(self, wavelength: str):
        """
        gets the power of a specific wavelength
        :param wavelength: wavelength string to get the power of (eg 400nm)
        :return:
        """
        if not self.get_wavelength_command:
            self.logger.error("get_wavelength call without get_wavelength_command")
            return None

        cmd = None
        if "{wavelength}" in self.get_wavelength_command:
            cmd = self.get_wavelength_command.format(wavelength=wavelength)

        return self._run_command(cmd)


class TelNetController(Controller):
    """
    controller for a telnet device
    """

    def __init__(self, config_section):
        self.ip = \
            self.telnet_port = ""
        super(TelNetController, self).__init__(config_section)

    def _run_command(self, cmd: str) -> bool:
        """
        sends a telnet command to the host
        :param cmd:
        :return: bool successful
        """
        telnet = Telnet(self.ip, self.telnet_port, 60)
        response = telnet.read_until(b'>', timeout=0.1)
        self.logger.debug("Intial response is: {0!s}".format(response.decode()))

        # we MUST wait a little bit before writing to ensure that the stream isnt being written to.
        time.sleep(0.5)
        # encode to ascii and add LF. unfortunately this is not to the telnet spec (it specifies CR LF or LF CR I'm ns)
        asciicmd = cmd.encode("ascii") + b"\n"
        telnet.write(asciicmd)
        # loopwait for 10 seconds with 0.01 second timeout until we have an actual response from the server
        cmd_response = b''
        for x in range(0, int(10.0 / 0.01)):
            cmd_response = telnet.read_until(b'>', timeout=0.01)
            if cmd_response:
                break
        else:
            self.logger.error("no response from telnet.")

        time.sleep(0.2)
        cmd_response = cmd_response.decode('ascii')
        if 'OK' in cmd_response:
            self.logger.debug("cmd response: {}".format(cmd_response))
            telnet.close()
            return True
        elif 'Error' in cmd_response:
            # raise ValueError('Light parameter error.\ncmd: "{}"\nresponse: "{}"'.format(cmd_response, cmd))
            self.logger.critical('Light parameter error.\ncmd: "{}"\nresponse: "{}"'.format(cmd_response, cmd))
            telnet.close()
            return False
        else:
            telnet.close()
            return False


class HTTPController(Controller):
    def __init__(self, config_section):
        self.ip = self.control_uri = ""
        super(HTTPController, self).__init__(config_section)
        if not self.ip.startswith("http://"):
            self.ip = "http://" + self.ip
        if not self.control_uri.startswith("/"):
            self.control_uri = "/" + self.control_uri

    def _run_command(self, cmd):
        payload = json.loads("{" + cmd + "}")
        response = requests.post(self.ip + self.control_uri, data=payload)
        if response.status_code == 200:
            return True
        else:
            return False

    def kill_schedule(self):
        """
        turns off all the heliospectras internal scheduling sysm
        :return:
        """
        # this is the payload, every timepoint (represented by "A##") is set to 0 or off
        payload = {
            'A00': 0,
            'A01': 0,
            'A02': 0,
            'A03': 0,
            'A04': 0,
            'A05': 0,
            'A06': 0,
            'A07': 0,
            'A08': 0,
            'A09': 0,
            'A10': 0,
            'A11': 0,
            'A12': 0,
            'A13': 0,
            'Submit': 'Set schedule'
        }
        response = requests.post(self.ip + "/cgi-bin/sched.cgi", data=payload)
        if response.status_code == 200:
            return True
        else:
            return False


class HelioSpectra(object):
    """
    Dumb runnner for a light, must be controlled by a chamber.
    automatically scales the power from 0-100 to the provided min-max (0-1000) by default
    """
    accuracy = 3
    s10wls = ["400nm", "420nm", "450nm", "530nm", "630nm", "660nm", "735nm"]
    s20wls = ["370nm", "400nm", "420nm", "450nm", "530nm", "620nm", "660nm", "735nm", "850nm", "6500k"]

    def __init__(self, config):
        self.name = config.get("name")

        self.logger = logging.getLogger(self.name)
        self.logger.info("init...")
        self.config = config.copy()
        self.failed = list()

        telnet_config = self.config.get('telnet', {})
        telnet_config['ip'] = self.config.get('ip')
        telnet_config['max'] = self.config.get('max_power', 1000)
        telnet_config['min'] = self.config.get('min_power', 0)
        self.controller = TelNetController(telnet_config)

        http_config = config.get("http", {})
        http_config['ip'] = self.config.get('ip')
        http_config['max'] = self.config.get('max_power', 1000)
        http_config['min'] = self.config.get('min_power', 0)
        self.logger.info("Killing the schedule")
        HTTPController(http_config).kill_schedule()
        # self.wavelengths = self.config.get("wavelengths",
        #                                    fallback=["400nm", "420nm", "450nm", "530nm", "630nm", "660nm", "735nm"])
        # these are the s20 wls.
        self.wavelengths = self.config.get("wavelengths",
                                           fallback=["370nm", "400nm", "420nm", "450nm", "530nm", "620nm", "660nm",
                                                     "735nm", "850nm", "6500k"])

    def set(self, intensities: list) -> dict:
        """
        sets the lights wavelengths to the values in the list.
        
        returns a dict of the wavelengths set and their values.
         
        If the length of the list of intensities doesnt match the number of custom wavelengths, but does match the 
        length of the list of s10 or s20 wavelengths, then they will be used.
        
        If none of those conditions are met, returns an empty dict.
        
        :param intensities: intensities to set to
        :return: 
        """
        values = dict(zip(self.wavelengths, intensities))
        if len(intensities) != len(self.wavelengths):
            if len(intensities) == len(HelioSpectra.s10wls):
                values = dict(zip(HelioSpectra.s10wls, intensities))
            elif len(intensities) == len(HelioSpectra.s20wls):
                values = dict(zip(HelioSpectra.s20wls, intensities))
            else:
                return {}
        if self.controller.set_all_wavelengths(values):
            return values
        return {}
