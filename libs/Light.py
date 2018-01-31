import re
import traceback
import datetime
import operator
import logging.config
import time
from telnetlib import Telnet
import json
import requests
import serial

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
        if percent is not None:
            power = int(self.max * (percent / 100) + self.min)
        cmd = None
        if "{power}" in self.set_all_command:
            cmd = self.set_all_command.format(power=power)
        elif "{percent}" in self.set_all_command:
            cmd = self.set_all_command.format(percent=percent)
        else:
            self.logger.error("set_one no cmd")
            return None
        return self._run_command(cmd)

    def set_one(self, wl: str, power: int = None, percent: int = None):
        """
        sets a specific wavelength to a value
        either a power or a percent must be specified
        
        :param wl: string of wavelength name (eg 400nm)
        :param power: absolute power value
        :param percent: percent value, calculated from min/max.
        """
        if not self.set_wavelength_command:
            self.logger.error("set_one call without set_wavelength_command")
            return None
        if percent is not None:
            power = int(self.max * (percent / 100) + self.min)
        cmd = None
        if "{power}" in self.set_wavelength_command:
            cmd = self.set_wavelength_command.format(wavelength=wl, power=power)
        elif "{percent}" in self.set_wavelength_command:
            cmd = self.set_wavelength_command.format(wavelength=wl, percent=percent)
        else:
            self.logger.error("set_one no cmd")
            return None
        return self._run_command(cmd)

    def set_all_each(self, values: dict, percent=True):
        """
        sets all wavelengths to specific values.
        only absolute values may be specified
        values should be specified as a dict of wavelength: value pairs

        :param values: dict of wavelengths and their respective values
        :param percent: whether the values are expressed as 0-100 or absolute.
        """

        if not self.set_all_wavelength_command:
            self.logger.error("set_all_each call without set_all_wavelength_command")
            return None
        r = re.compile(r'\d+')

        def keygetter(it):
            vs = r.search(it[0])
            return 0 if not vs else int(vs.group())

        sorted_values = sorted(values.items(), key=keygetter)
        if percent is not None:
            sorted_values = [(k, int(self.max * (v / 100) + self.min)) for k, v in sorted_values]

        if len(values) < self.set_all_wavelength_command.count("{}"):
            self.logger.error("Not enough wavelengths specified for set_all_each, padding with 0s")
            diff = self.set_all_wavelength_command.count("{}") - len(values)
            sorted_values.extend(("padded", 0) for _ in range(diff))
        sorted_values = [(k, clamp(v, self.min, self.max)) for k, v in sorted_values]
        cmd = self.set_all_wavelength_command.format(*[v for k, v in sorted_values])
        if self._run_command(cmd):
            return dict([(str(k).lower(), int(v)) for k, v in sorted_values])
        return {}

    def get_one(self, wavelength: str):
        """
        gets the power of a specific wavelength
        :param wavelength: wavelength string to get the power of (eg 400nm)
        :return:
        """
        if not self.get_wavelength_command:
            self.logger.error("get_one call without get_wavelength_command")
            return None

        cmd = None
        if "{wavelength}" in self.get_wavelength_command:
            cmd = self.get_wavelength_command.format(wavelength=wavelength)

        return self._run_command(cmd)


class TelNetController(Controller):
    """
    controller for a Light.
    """

    def __init__(self, config_section):
        self.ip = \
            self.telnet_port = ""
        super(TelNetController, self).__init__(config_section)

    def _run_command(self, cmd: str, ok="OK") -> bool:
        """
        sends a telnet command to the host
        :param cmd:
        :return: bool successful
        """

        telnet = Telnet(self.ip, self.telnet_port, 60)
        try:
            response = telnet.read_until(b'>', timeout=0.1)
            self.logger.debug("Intial response is: {0!s}".format(response.decode()))

            # we MUST wait a little bit before writing to ensure that the stream isnt being written to.
            time.sleep(0.5)
            # encode to ascii and add LF. unfortunately this is not to the telnet spec (it specifies CR LF or LF CR I'm ns)
            telnet.write(cmd.encode("ascii") + b"\n")
            ok_regex = re.compile(b'.*'+ok.encode("ascii")+b'.*')
            response = telnet.expect([ok_regex], timeout=30)
            if response[0] < 0:
                return False
            else:
                return True
        except:
            self.logger.error(traceback.format_exc())
            return False
        finally:
            telnet.close()


class PSISerialInterfaceController(Controller):
    """
    Controller for Photon Systems Instruments lights.
    """

    def __init__(self, address, available_channels=(0, 1, 3, 4, 5, 6, 7, 8)):
        super(PSISerialInterfaceController, self).__init__({})
        self.available_channels = available_channels
        self.address = address

        for x in range(5):
            try:
                self.ser = serial.Serial('/dev/ttyUSB{}'.format(x), 9600, bytesize=serial.EIGHTBITS,
                              parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                              rtscts=False, dsrdtr=False, xonxoff=False)
                break
            except:
                traceback.print_exc()
                time.sleep(0.5)
                continue
        self.max = 1022
        self.min = 1000

    def __del__(self):
        try:
            self.ser.close()
        except:
            traceback.print_exc()
            pass

    def construct_packet(self, channel: int,
                         value: int,
                         operation: int = 0,
                         t: int = 1) -> bytearray:
        """
        Constructs a packet according to the protocol laid out by PSI to set a channel to value. G.

        :param channel: channel to set
        :param value: value between 0-1022
        :param operation: operation, should be 0 for set.
        :param t: type, apparently should be 1 for us in psi spec
        :return: bytearray fo the packet
        """
        if channel not in self.available_channels:
            pass
            # raise ValueError("Channel < {} > is not in available channels 0 ,1 ,3 ,4, 5, 6, 7, 8".format(channel))

        if not (0 <= value < 1022):
            raise ValueError("Value < {} > is not within range 0-1022 specified for PSI lights".format(value))

        # header - always ST P.
        packet = [ord('S'), ord('T'), 0, 0, 0, 0, 0]

        # target + nibble of address P.
        packet[2] = (t << 4) | ((self.address >> 8) & 0x0F)
        # the rest of the address P.
        packet[3] = self.address & 0xFF

        # opcode + payload P.
        # opcode = 4b channel + 2b instr P.
        # is 'instr' meant to be operation? G.
        # either way this should now be working... G.
        packet[4] = (((channel << 4) & 0xF0) | ((operation << 2) & 0x0C) | ((value >> 8) & 0x03))
        packet[5] = value & 0xFF
        # checksum for last byte of packet.
        chk = 0
        for byt in packet[:6]:
            chk ^= byt
        packet[6] = chk & 0xFF
        return bytearray(packet)

    def activate(self, c):
        """
        helper to get an activation packet.
        :param c:
        :return:
        """
        activate_packet = self.construct_packet(c, 1, operation=2)
        activate_packet += self.construct_packet(c, 1, operation=3)
        self.ser.write(activate_packet)

    def set_one(self, c: int, power: int = None, percent: int = None):
        """
        set one channel re the psi "spec"

        :param c: channel number
        :param power: power in 0-1022
        :param percent: power percentage (0-100)
        """
        if percent is not None:
            power = int(self.max * (percent / 100) + self.min)
        # actvate the channel for light
        self.activate(c)
        intensity_packet = self.construct_packet(c, power, operation=0)
        intensity_packet += self.construct_packet(c, power, operation=1)
        self.ser.write(intensity_packet)

    def set_all(self, power: int = None, percent: int = None):
        for c in self.available_channels:
            self.set_one(c, power=power)
        return True

    def set_all_each(self, values: dict, percent=True):
        """
        sets all wavelengths to specific values.
        only absolute values may be specified
        values should be specified as a dict of wavelength: value pairs

        :param values: dict of channels and their respective values
        :param percent: whether the values are expressed as 0-100 or absolute.
        """
        try:
            if percent:
                for c, v in values.items():
                    self.set_one(c, percent=v)
            else:
                for c, v in values.items():
                    self.set_one(c, power=v)
            return dict(("chan-{}".format(k), v) for k, v in values.items())
        except:
            traceback.print_exc()
            return {}


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
        response = requests.post(self.ip + self.control_uri, data=payload, timeout=10)
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
        try:
            response = requests.post(self.ip + "/cgi-bin/sched.cgi", data=payload, timeout=10)
            if response.status_code == 200:
                return True
            else:
                return False
        except:
            return False


class PSILight(object):
    """
    runner for PSI serial lights
    """
    accuracy = 3
    default_available_channels = (0, 1, 3, 4, 5, 6, 7, 8)

    def __init__(self, config):
        self.name = config.get("name")

        self.logger = logging.getLogger(self.name)
        self.logger.info("PSI light init...")
        self.config = config.copy()
        self.failed = list()

        self.percent = config.get("percent", True)

        self.min = 0
        # 1022 max value as per psi spec.
        self.max = 1022

        self.address = int(self.config.get("address", 1))
        self.controller = PSISerialInterfaceController(self.address)

        self.available_channels = self.config.get("available_channels",
                                           self.default_available_channels)

    def set(self, intensities: list) -> dict:
        """
        sets the lights channels to the values in the list.

        returns a dict of the channels set and their values.

        If the length of the list of intensities doesnt match the number of custom wavelengths, but does match the
        length of the list of s10 or s20 wavelengths, then they will be used.

        If none of those conditions are met, returns an empty dict.

        :param intensities: ordered list of intensities to set
        :return: dict of set values
        """
        intensities = list(map(float, intensities))

        values = dict(zip(self.available_channels, intensities))

        return self.controller.set_all_each(values, percent=True)

class HelioSpectra(object):
    """
    Dumb runnner for a light, must be controlled by a chamber.
    automatically scales the power from 0-100 to the provided min-max (0-1000) by default
    """
    accuracy = 3
    # s10 wls
    s10wls = ["400nm", "420nm", "450nm", "530nm", "630nm", "660nm", "735nm"]

    # these are the s20 wls.
    s20wls = ["370nm", "400nm", "420nm", "450nm", "530nm", "620nm", "660nm", "735nm", "850nm", "6500k"]

    def __init__(self, config):
        self.name = config.get("name")

        self.logger = logging.getLogger(self.name)
        self.logger.info("Helio init...")
        self.config = config.copy()
        self.failed = list()

        self.percent = config.get("percent", True)
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
        self.wavelengths = self.config.get("wavelengths", self.s20wls)

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
        intensities = list(map(float, intensities))

        values = dict(zip(self.wavelengths, intensities))
        if len(intensities) != len(self.wavelengths):
            if len(intensities) == len(HelioSpectra.s10wls):
                values = dict(zip(HelioSpectra.s10wls, intensities))
            elif len(intensities) == len(HelioSpectra.s20wls):
                values = dict(zip(HelioSpectra.s20wls, intensities))
            else:
                print("light values do not match length, {}".format(str(intensities)))
                return {}
        print("Setting light values: {}".format(str(values)))

        return self.controller.set_all_each(values, percent=True)
