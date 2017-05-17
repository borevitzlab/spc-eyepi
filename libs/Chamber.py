import datetime
import logging.config
import time
from telnetlib import Telnet
from threading import Thread, Event
from libs.SysUtil import SysUtil
import re
import os
from .Light import HelioSpectra

try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

try:
    import telegraf
except Exception as e:
    logging.error("Couldnt import Telegraf, not sending metrics: {}".format(str(e)))


def clamp(v: float, minimum: float, maximum: float) -> float:
    """
    clamps a number to the minimum and maximum.
    
    :param v:
    :param minimum:
    :param maximum:
    :return:
    """
    return min(max(v, minimum), maximum)


TIMEOUT = 60
shell_re = re.compile(b"#")


class ConvironTelNetController(object):
    """
    controller for a telnet device
    """

    def __init__(self, config_section):
        self.telnet_host = \
            self.telnet_port = \
            self.telnet_user = \
            self.telnet_password = ""
        self.set_command = "pcoset"
        self.get_command = "pcoget"
        self.device_id = 0
        self.cmd_str = "{cmd} {device_id}".format(cmd=self.set_command, device_id=self.device_id)
        self.get_cmd_str = "{cmd} {device_id}".format(cmd=self.get_command, device_id=self.device_id)

        self.init_sequence = ["I 100 26", "I 101 1", "I 102 1"]
        self.teardown_sequence = ["I 123 1", "I 121 1"]
        self.reload_sequence = ["I 100 7", "I 101 1", "I 102 1"]
        self.clear_write = "I 120 0"
        self.clear_busy = "I 123 0"
        self.temp_index = 105
        self.humidity_index = 106
        self.temp_dtype = "I"
        self.humidity_dtype = "I"


        # these all seem to be indexes into the same data
        self.get_temp_command = "A 1 2"  # get from A row, index 1, count 2
        self.get_humidity_command = "I 4 2"  # get from I row, index 4, count 2
        self.get_par_command = "I 11"  # count is optional

        self.logger = logging.getLogger(str(self.__class__))

        # anthing above this line is default
        for k, v in config_section.items():
            setattr(self, k, v)

        self._init_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in self.init_sequence]
        self._teardown_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in
                                   self.teardown_sequence]
        self._reload_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in self.reload_sequence]
        self._clear_write = bytes("{} {}".format(self.cmd_str, self.clear_write), encoding="UTF")
        self._clear_busy = bytes("{} {}".format(self.cmd_str, self.clear_busy), encoding="UTF")

        # set temperature and humidity. 105, 106 are the data indices for them.
        self._set_temp = bytes("{} {} {} {{}}".format(self.cmd_str, self.temp_dtype, self.temp_index),
                               encoding="UTF")
        self._set_humidity = bytes("{} {} {} {{}}".format(self.cmd_str, self.humidity_dtype, self.humidity_index),
                                   encoding="UTF")

        # get temp, humidity and par
        self._get_temp = bytes("{} {}".format(self.get_cmd_str, self.get_temp_command), encoding="UTF")
        self._get_humidity = bytes("{} {}".format(self.get_cmd_str, self.get_humidity_command), encoding="UTF")
        self._get_par = bytes("{} {}".format(self.get_cmd_str, self.get_par_command), encoding="UTF")

    def _run(self, telnet, command, expected):
        """Do the leg work between this and the conviron."""

        self.logger.debug("Sending command:  {0!s}".format(command.decode()))
        telnet.write(command + b'\n')
        response = telnet.expect([expected, ], timeout=TIMEOUT)
        self.logger.debug("Received:  {0!s}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Expected response was not received")
        return response

    def _connect_login(self):
        telnet = Telnet(self.telnet_host)
        response = telnet.expect([re.compile(b'login'), ], timeout=TIMEOUT)

        self.logger.debug("Intial response is: {0!s}".format(response.decode()))
        if response[0] < 0:
            raise RuntimeError("Login prompt not recieved")
        # we MUST wait a little bit before writing to ensure that the stream isnt being written to.
        time.sleep(0.1)
        payload = bytes(self.telnet_user + "\n", encoding="UTF8")
        telnet.write(payload)

        response = telnet.expect([re.compile(b"Password:"), ], timeout=TIMEOUT)
        self.logger.debug("Sent username: {0!s}".format(payload.decode()))
        self.logger.debug("Received: {0!s}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Password prompt was not received")
        # Password
        payload = bytes(self.telnet_password + "\n", encoding="UTF8")
        telnet.write(payload)

        response = telnet.expect([shell_re, ], timeout=TIMEOUT)
        self.logger.debug("Send password: {0!s}".format(payload.decode()))
        self.logger.debug("Received: {}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Shell prompt was not received")

        return telnet

    def _clear_write_unflag(self, telnet):
        time.sleep(2)
        self._run(telnet, self._clear_write, shell_re)

    def _clear_busy_unflag(self, telnet):
        time.sleep(2)
        self._run(telnet, self._clear_busy, shell_re)

    def set(self, temperature=None, humidity=None) -> bool:
        """
        sends a telnet command to the host
        
        :param cmd:
        :return: bool successful
        """
        telnet = self._connect_login()
        try:

            set_commands = [bytes(self._set_temp.decode().format(temperature), encoding="UTF8"),
                            bytes(self._set_humidity.decode().format(humidity), encoding="UTF8")]
            for cmd in self._init_sequence + set_commands + self._teardown_sequence:
                self._run(telnet, cmd, shell_re)
            self._clear_write_unflag(telnet)

            time.sleep(2)
            for cmd in self._reload_sequence + self._teardown_sequence:
                self._run(telnet, cmd, shell_re)
        except Exception as e:
            self.logger.error("Error setting values {}".format(str(e)))
            return False
        finally:
            self._clear_write_unflag(telnet)
            self._clear_busy_unflag(telnet)
            time.sleep(2)
            telnet.close()

        return True

    def get_values(self):
        telnet = self._connect_login()
        values = {"temp_recorded": None,
                  "temp_set": None,
                  "humidity_recorded": None,
                  "par": None}
        try:
            temp_resp = self._run(telnet, self._get_temp, re.compile(b"\d+"))
            if temp_resp[0] <= 1:
                self.logger.error("Less than two values returned for temperature")
            try:
                values['temp_recorded'] = temp_resp[2][1]
            except:
                pass
            try:
                values['temp_set'] = temp_resp[2][0]
            except:
                pass

            hum_resp = self._run(telnet, self._get_humidity, re.compile(b"\d+"))
            if hum_resp[0] <= 1:
                self.logger.error("Less than two values returned for humidity")
            try:
                values['humidity_recorded'], values['humidity_set'] = hum_resp[2]
            except:
                pass

            par_resp = self._run(telnet, self._get_par, re.compile(b"\d+"))
            if par_resp[0] <= 0:
                self.logger.error("No values returned for par")
            try:
                values['par'] = par_resp[2][0]
            except:
                pass

            time.sleep(2)
            for cmd in self._reload_sequence + self._teardown_sequence:
                self._run(telnet, cmd, shell_re)
        except Exception as e:
            self.logger.error("Error setting values {}".format(str(e)))
            return values
        finally:
            self._clear_write_unflag(telnet)
            self._clear_busy_unflag(telnet)
            time.sleep(2)
            telnet.close()
        return values


class Chamber(object):
    """
    Schedule runner for a chamber.
    """
    accuracy = 60

    def __init__(self, identifier: str = None, config=None):
        # identifier is NOT OPTIONAL!
        # init with name or not, just extending some of the functionality of Thread

        # self.communication_queue = queue or deque(tuple(), 256)
        self.logger.info("Init...")
        if not os.path.isdir("data"):
            os.mkdir("data")
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier
        self.config = {}
        try:
            self.config = config.copy()
        except:
            pass

        self.temperature_multiplier = self.config.get("temperature_multiplier", 10.0)

        self.controller = \
            self.csv = \
            self.out_of_range = \
            self.current_timepoint = None
        self.lights = []
        light_configs = self.config.get("lights", [])
        for lc in light_configs:
            l = HelioSpectra(lc)
            self.lights.append(l)

        self.datetimefmt = None
        self._current_temp = float()
        self._current_wavelength_intentisies = list()
        self._current_humidity = float()
        self._current_csv_index = 0

        telnet_config = self.config.get('telnet', {})
        data_fp = self.config.get("slc_datafile") or self.config.get("csv_datafile")

        self.controller = ConvironTelNetController(dict(telnet_config))

        self.datetimefmt = None

        self.csv = SysUtil.load_or_fix_solarcalc(data_fp)
        self.logger.info("Loaded {}".format(data_fp))

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
        determines the current state the chamber should be in.
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
        self._current_wavelength_intentisies = self.csv[self._current_csv_index][3:-1]
        self._current_temp = self.csv[self._current_csv_index][1] * self.temperature_multiplier
        self._current_humidity = self.csv[self._current_csv_index][2]

    def test(self):
        """
        testing function, to test whether the chamber sends the correct values. 
        """
        self.current_timepoint = self.csv[-1][0] - datetime.timedelta(hours=12)
        date_end = self.csv[-1][0] + datetime.timedelta(days=4)
        self.logger.info("Running from {} to {}".format(self.current_timepoint.strftime("%Y-%m-%d %H:%M"),
                                                        date_end.strftime("%Y-%m-%d %H:%M")))
        while self.current_timepoint < date_end:
            self.current_timepoint = self.current_timepoint + datetime.timedelta(minutes=5)
            temp, hum = self._current_temp, self._current_humidity
            self.calculate_current_state()
            if temp != self._current_temp or hum != self._current_humidity:
                if self.out_of_range:
                    self.logger.warning("Running outside of Solarcalc file time range. Repeating the last 24 hours")
                self.logger.info("#{0:05d} @ {1} - {2}:{3}".format(self._current_csv_index,
                                                                   self.current_timepoint.strftime("%Y-%m-%d %H:%M"),
                                                                   temp, hum))
                self.controller.set(temperature=self._current_temp,
                                    humidity=self._current_humidity)

    def stop(self):
        self.stopper.set()

    def run(self):
        """
        runs the chamber continuously.
        """
        while True and not self.stopper.is_set():
            self.current_timepoint = datetime.datetime.now()
            temp, hum = self._current_temp, self._current_humidity
            wavelength_intensities = self._current_wavelength_intentisies
            self.calculate_current_state()

            if temp != self._current_temp or hum != self._current_humidity:
                if self.out_of_range:
                    self.logger.warning("Running outside of Solarcalc file time range. Repeating the last 24 hours")
                self.logger.info("#{0:05d} @ {1} - {2}:{3}".format(self._current_csv_index,
                                                                   self.current_timepoint.strftime("%Y-%m-%d %H:%M"),
                                                                   temp, hum))
                self.controller.set(temperature=self._current_temp,
                                    humidity=self._current_humidity)
            if wavelength_intensities != self._current_wavelength_intentisies:

                telegraf_client = telegraf.TelegrafClient(host="localhost", port=8092)
                for light in self.lights:
                    try:
                        lightvalues = light.set(self._current_wavelength_intentisies)
                        telegraf_client.metric("lights", lightvalues, tags={"name": light.name})
                    except Exception as e:
                        self.logger.error("Error uopdating lights")
            try:
                measurement = self.controller.get_values()
                measurement['temp_target'] = self._current_temp
                measurement['humidity_target'] = self._current_humidity
                measurement['temp_recorded'] /= self.temperature_multiplier
                telegraf_client = telegraf.TelegrafClient(host="localhost", port=8092)
                telegraf_client.metric("conviron", measurement)

                self.logger.debug("Communicated sesor data to telegraf")
            except Exception as exc:
                self.logger.error("Couldnt communicate with telegraf client. {}".format(str(exc)))

            time.sleep(self.accuracy * 2)


class ThreadedChamber(Thread, Chamber):
    """
    threaded implementation.
    """

    def __init__(self, *args, **kwargs):
        if hasattr(self, "identifier"):
            Thread.__init__(self, name=self.identifier)
        else:
            Thread.__init__(self)

        print("Threaded startup")
        super(ThreadedChamber, self).__init__(*args, **kwargs)
        self.daemon = True
