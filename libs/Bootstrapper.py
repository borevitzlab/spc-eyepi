__author__ = 'Gareth Dunstone'
from threading import Thread, Event
import json
import time
import urllib
from configparser import ConfigParser

from schedule import Scheduler

from .AESCipher import AESCipher


class Bootstrapper(Thread):
    def __init__(self):
        Thread.__init__(self)
        self.scheduler = Scheduler()
        self.scheduler.every(6).hours.do(self.go)
        self.stopper = Event()

    def go(self):
        try:
            jsondata = {}
            with open("/home/tor_private/hostname") as f:
                tor_hostname = f.read().replace('\n', '')
            jsondata["onion_address"] = tor_hostname.split(" ")[0]
            jsondata["onion_cookie_auth"] = tor_hostname.split(" ")[1]
            jsondata["onion_cookie_client"] = tor_hostname.split(" ")[-1]
            rpiconfig = ConfigParser()
            rpiconfig.read("picam.ini")
            ciphertext = AESCipher(rpiconfig["ftp"]['pass']).encrypt(json.dumps(jsondata))
            tries = 0
            data = urllib.parse.urlencode({'m': ciphertext})
            data = data.encode('utf-8')
            req = urllib.request.Request('http://phenocam.org.au/hidden', data)
            while tries < 100:
                data = urllib.request.urlopen(req)
                if data.getcode() == 200:
                    break
                time.sleep(10)
                tries += 1
        except Exception as e:
            print(str(e))

    def stop(self):
        self.stopper.set()

    def run(self):
        while True and not self.stopper.is_set():
            self.scheduler.run_pending()
            time.sleep(1)
