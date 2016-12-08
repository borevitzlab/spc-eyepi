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
        pass

    def set_all(self, power: int = None, percent: int = None):
        """
        sets all wavelengths to either an absolute value or a percentage of the total
        :param power:
        :param percent:
        :return:
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
        self.telnet_host = \
            self.telnet_port = ""
        super(TelNetController, self).__init__(config_section)

    def _run_command(self, cmd: str) -> bool:
        """
        sends a telnet command to the host
        :param cmd:
        :return: bool successful
        """
        telnet = Telnet(self.telnet_host, self.telnet_port, 60)
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
        self.url_host = self.control_uri = ""
        super(HTTPController, self).__init__(config_section)
        if not self.url_host.startswith("http://"):
            self.url_host = "http://" + self.url_host
        if not self.control_uri.startswith("/"):
            self.control_uri = "/" + self.control_uri

    def _run_command(self, cmd):
        payload = json.loads("{" + cmd + "}")
        response = requests.post(self.url_host + self.control_uri, data=payload)
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
        response = requests.post(self.url_host + "/cgi-bin/sched.cgi", data=payload)
        if response.status_code == 200:
            return True
        else:
            return False


class Light(object):
    """
    Schedule runner for a light.
    """
    accuracy = 3

    def __init__(self, identifier: str = None, queue: deque = None, **kwargs):
        # identifier is NOT OPTIONAL!
        # init with name or not, just extending some of the functionality of Thread

        self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier
        self.config_filename = SysUtil.ensure_light_config(self.identifier)
        self.config = \
            self.controller = \
            self.wavelengths = \
            self.csv = \
            self.out_of_range = \
            self.current_timepoint = None
        self.datetimefmt = None
        self._current_wavelength_intentisies = dict()
        self._current_csv_index = 0
        self.failed = list()
        self.re_init()

    def re_init(self):
        """
        re-initialisation.
        this causes all the confiuration values to be reacquired, and a config to be recreated as valid if it is broken.
        :return:
        """
        self.logger.info("Re-init...")
        self.config = SysUtil.ensure_light_config(self.identifier)

        telnet_config = dict(self.config['telnet'])
        telnet_config['max'] = self.config['light']['max_power']
        telnet_config['min'] = self.config['light']['min_power']
        self.controller = TelNetController(dict(telnet_config))
        http_config = dict(self.config['url'])
        http_config['max'] = self.config['light']['max_power']
        http_config['min'] = self.config['light']['min_power']
        self.logger.info("Killing the schedule")
        HTTPController(dict(http_config)).kill_schedule()

        self.datetimefmt = None

        wavelengths = self.config.get("light", "wavelengths", fallback="400nm,420nm,450nm,530nm,630nm,660nm,735nm")
        self.wavelengths = [s.strip() for s in wavelengths.split(",")]
        data_fp = SysUtil.get_light_datafile(self.identifier)
        self.csv = SysUtil.load_or_fix_solarcalc(data_fp)
        self.logger.info("Loaded {}".format(data_fp))

        self._current_wavelength_intentisies = {wl: 0 for wl in self.wavelengths}
        self._current_csv_index = 0

        def parse_datestring(datestring: str) -> datetime.datetime:
            """
            parses a datestring into a datetime.
            first tries the member self.datetimefmt to speed it up.
            Then tries getting it from timestamp (unix style)
            and then descending accuracies (and standardisation) of timestamp.
            the order and list is as follows:
            iso8601 datetime accurate to microseconds with timezone
            iso8601 datetime accurate to microseconds
            iso8601 datetime accurate to seconds with timezone
            iso8601 datetime accurate to seconds
            iso8601 datetime accurate to minutes
            timestream format (YY_mm_DD_HH_MM_SS) accurate to seconds
            timestream format accurate to minutes
            Alternate date format (YY/mm/DD) accurate to seconds
            Alternate date format accurate to minutes
            iso8601 with reverse ordered date part accurate to seconds
            iso8601 with reverse ordered date part accurate to minutes
            timestream format with reverse ordered date part accurate to seconds
            timestream format with reverse ordered date part accurate to minutes
            Alternate date format with reverse ordered date part accurate to seconds
            Alternate date format with reverse ordered date part accurate to minutes

            :param datestring: string to parse
            :rtype datetime:
            :return: datetime
            """
            datetime_fmts = ["%Y-%m-%dT%H:%M:%S.%f%z",
                             "%Y-%m-%dT%H:%M:%S.%f",
                             "%Y-%m-%dT%H:%M:%S%z",
                             "%Y-%m-%dT%H:%M:%SZ",
                             "%Y-%m-%d %H:%M:%S",
                             "%Y-%m-%d %H:%M",
                             "%Y_%m_%d_%H_%M_%S",
                             "%Y_%m_%d_%H_%M",
                             "%Y/%m/%d %H:%M:%S",
                             "%Y/%m/%d %H:%M",
                             "%d-%m-%Y %H:%M:%S",
                             "%d-%m-%Y %H:%M",
                             "%d_%m_%Y_%H_%M_%S",
                             "%d_%m_%Y_%H_%M",
                             "%d/%m/%Y %H:%M:%S",
                             "%d/%m/%Y %H:%M"]

            if self.datetimefmt:
                try:
                    return datetime.datetime.strptime(datestring, self.datetimefmt)
                except:
                    pass
            try:
                return datetime.datetime.fromtimestamp(datestring)
            except:
                pass
            for fmt in datetime_fmts:
                try:
                    q = datetime.datetime.strptime(datestring, fmt)
                    self.datetimefmt = fmt
                    return q
                except:
                    pass
            else:
                raise ValueError("Error parsing {} to a valida datetime".format(str(datestring)))

        self.current_timepoint = datetime.datetime.now()
        self.out_of_range = self.current_timepoint > self.csv[-1][0]

    def calculate_current_state(self):
        """
        determines the current state the lights should be in.
        doesnt send the state.
        sets the internal state of the Light object
        :param nowdt:
        :return:
        """

        def nfunc(in_dt: datetime.datetime) -> bool:
            """
            returns true if the input time is greater than that of the current csv
            :return:
            """
            csvdt = self.csv[self._current_csv_index][0]
            return in_dt >= csvdt

        current_timepoint = self.current_timepoint

        if self.out_of_range:
            last = self.csv[-1][0]
            current_timepoint = current_timepoint.replace(year=last.year, month=last.month, day=last.day)

        while nfunc(current_timepoint):
            # print(current_timepoint, self.csv[self._current_csv_index%len(self.csv)][0])
            self._current_csv_index += 1
            if self._current_csv_index >= len(self.csv):
                self.out_of_range = True
                self._current_csv_index = len(self.csv) - 1
                while (self.csv[-1][0] - datetime.timedelta(hours=24)) < self.csv[self._current_csv_index][0]:
                    self._current_csv_index -= 1
                break
        self._current_wavelength_intentisies = dict(zip(self.wavelengths,
                                                        self.csv[self._current_csv_index][3:-1]))

    def test(self):
        self.current_timepoint = self.csv[-1][0] - datetime.timedelta(hours=12)
        date_end = self.csv[-1][0] + datetime.timedelta(days=4)
        self.logger.info("Running from {} to {}".format(self.current_timepoint.strftime("%Y-%m-%d %H:%M"),
                                                        date_end.strftime("%Y-%m-%d %H:%M")))
        while self.current_timepoint < date_end:
            self.current_timepoint = self.current_timepoint + datetime.timedelta(minutes=5)
            wl = self._current_wavelength_intentisies
            self.calculate_current_state()
            if wl != self._current_wavelength_intentisies:
                s = "  ".join(
                    wl + ":" + i.zfill(3) for wl, i in sorted(self._current_wavelength_intentisies.items(),
                                                              key=operator.itemgetter(0)))
                # if self.out_of_range:
                #     self.logger.warning("Running outside of Solarcalc file time range. Repeating the last 24 hours")
                self.logger.info("#{0:05d} @ {1} - {2}".format(self._current_csv_index,
                                                               self.current_timepoint.strftime("%Y-%m-%d %H:%M"), s))
                self.send_state()

    def send_state(self):
        while not self.controller.set_all_wavelengths(self._current_wavelength_intentisies):
            self.logger.error("Failure running telnet command.")

    def stop(self):
        self.stopper.set()

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
        :return:
        """
        try:
            data = dict(
                name="Light-" + self.identifier,
                identifier=self.identifier,
                failed=self.failed,
                last_timepoint=int(self.current_timepoint.strftime("%s")))
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("thread communication error: {}".format(str(e)))

    def run(self):
        while True and not self.stopper.is_set():
            self.current_timepoint = datetime.datetime.now()
            wl = self._current_wavelength_intentisies
            self.calculate_current_state()
            if wl != self._current_wavelength_intentisies:
                s = " ".join(wl + ":" + inte for wl, inte in self._current_wavelength_intentisies.items())
                if self.out_of_range:
                    self.logger.warning("Running outside of Solarcalc file time range. Repeating the last 24 hours")
                self.logger.info("#{0:05d} @ {1} - {2}".format(self._current_csv_index,
                                                               self.current_timepoint.strftime("%Y-%m-%d %H:%M"), s))

                self.send_state()
            time.sleep(1)


class ThreadedLights(Thread):
    """
    threaded implementation.
    """
    def __init__(self, *args, **kwargs):
        if hasattr(self, "identifier"):
            Thread.__init__(self, name=self.identifier)
        else:
            Thread.__init__(self)

        print("Threaded startup")
        super(ThreadedLights, self).__init__(*args, **kwargs)
        self.daemon = True
        if hasattr(self, "config_filename") and hasattr(self, "re_init"):
            SysUtil().add_watch(self.config_filename, self.re_init)
