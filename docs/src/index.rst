.. SpectroPhenoClimatron EyePI documentation master file, created by
   sphinx-quickstart on Thu Dec  8 12:53:48 2016.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

SPC-EyePi
=========

.. toctree::
   :maxdepth: 2
   :caption: Contents:

Credits/License
---------------

The goal of this project is to create a robust open source code base for controlling DSLR and the on-board Raspberry Pi camera from a Raspberry Pi or similar Linux boards.

This project is open source but please `contact us <https://github.com/borevitzlab>`_ before using the code so we can know who is using it and please make sure to link back here in any code you use. This project is in active development but we are happy to work with other groups to develop new features so drop us a line if you are interested.

This code was developed for the TraitCapture project at ANU.

Please cite: *Brown, Tim B., et al.* `TraitCapture : genomic and environment modelling of plant phenomic data. Current opinion in plant biology 18 (2014): 73-79 <http://www.sciencedirect.com/science/article/pii/S1369526614000181>`_. when using the code.


Features
--------

Currently supported and maintained:
 * Control of multiple DSLR cameras.
 * Upload of JPG, RAW or JPG+RAW images.
 * Captured photos are cached locally and  uploaded to a central server of your choice via sftp or ftp.
 * Cameras settings can be controlled from a web page.
 * Wifi and ethernet. Creates an ad-hoc wifi network no know network is found for easier config of new systems and in new wifi environments.
 * Camera uploads it's IP address along with image for easy finding of the camera on your network
 * Camera can auto-upload both timestamped and fixed filenames (for use as a webcam or with auto archiving systems requiring a fixed image name).
 * Extensively tested with Canons (600D, 700D, 70D). Works with Nikon D7100 but not extensively tested.
 * Ansible provisioning system for raspberry Pis running Arch Linux.

Planned or in development:
 * Time-series scheduler  to auto-set camera exposure and shutter speed settings (useful for shooting 24-hrs a day in fixed but variable lighting situations like plant growth chambers)
 * RAM spooling with sd card fallback to increase SD card life.
 * Auto Bulb-ramping... we are aspiring to be able to shoot 24-7 DSLR timelapses through sunrise and sunset.
 * DB-based camera management system.
 * Weatherproof and solar powered camera housing  for full remote timelapse solution

Automatically detects connected usb DSLRs and the Raspberry Pi camera (just duplicate *example.ini* to *picam.ini* and *eyepi.ini* and change the configuration values). DSLR's are given a unique ID based on hardware serial.

Camera name can be easily changed to a user friendly value.

Requirements
------------

*os:*
 * python3
 * python-cffi
 * exiv2
 * opencv 3.1 `[Arch-Extra] <https://www.archlinux.org/packages/extra/x86_64/opencv/>`_
 * tor (optional)

*python*
 * `flask <http://flask.pocoo.org/>`_
 * flask-bcrypt `[pip] <https://pypi.python.org/pypi/Flask-Bcrypt>`_
 * flask-login `[pip] <https://pypi.python.org/pypi/Flask-Login>`_
 * WTForms `[pip] <https://pypi.python.org/pypi/WTForms>`_
 * browsepy `[pip] <https://pypi.python.org/pypi/browsepy/0.4.0>`_
 * pyudev `[pip] <https://pypi.python.org/pypi/pyudev>`_
 * gphoto2-cffi `[git] <https://github.com/borevitzlab/gphoto2-cffi>`_
 * numpy
 * pillow `[pip] <https://pypi.python.org/pypi/Pillow/3.1.1>`_
 * picamera (optional) `[pip] <https://pypi.python.org/pypi/picamera/1.12>`_
 * py3exiv2 (optional) `[pip] <https://pypi.python.org/pypi/py3exiv2/0.2.1>`_
 * cryptography `[pip] <https://pypi.python.org/pypi/cryptography>`_
 * pysftp `[pip] <https://pypi.python.org/pypi/pysftp>`_
 * requests[socks] `[pip] <https://pypi.python.org/pypi/requests/2.11.1>`_
 * create_ap `[aur] <https://aur.archlinux.org/packages/create_ap>`_
 * schedule `[pip] <https://pypi.python.org/pypi/schedule>`_
 * pyyaml `[pip] <https://pypi.python.org/pypi/PyYAML/3.12>`_
 * RPi.GPIO (optional) `[pip] <https://pypi.python.org/pypi/RPi.GPIO/0.6.3>`_


Extra Details
-------------

If you are capturing using a Raspberry Pi camera you need to install **py3exiv2** if you want your images to have exif data.

There is code to support the IVMech IVPort, however its a little tricky to get working as there is no method to detect the device yet, and of course you need to install **RPi.GPIO**.

To get into the web interface using the wifi the default ip address is 192.168.12.1 and you will need to use the user admin:spceyepidefaultaccess (it must be running first).

SPC-EyePi also updates from the master branch of this repository.


Documentation Links
-------------------

* :doc:`ansible`
* :ref:`genindex`
* :ref:`modindex`
