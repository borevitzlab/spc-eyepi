This project is open source but please [contact us](https://github.com/borevitzlab) before using the code so we can know who is using it and please make sure to link back here in any code you use. This project is in active development but we are happy to work with other groups to develop new features so drop us a line if you are interested.

This code was developed for the TraitCapture project at ANU. Please cite: _Brown, Tim B., et al. [TraitCapture](http://www.sciencedirect.com/science/article/pii/S1369526614000181): genomic and environment modelling of plant phenomic data. Current opinion in plant biology 18 (2014): 73-79_. when using the code.

''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

**Welcome to SPC-EyePi**

The goal of this project is to create a robust open source code base for controlling DSLR and the on-board raspberry pi camera from a Raspberry-PI or similar Linux boards.

Features being actively supported and maintained:
 * Control of multiple DSLR cameras.
 * Upload of JPG, RAW or JPG+RAW images.
 * Captured photos are cached locally and  uploaded to a central server of your choice via sftp or ftp. 
 * Cameras settings can be controlled from a web page. 
 * Wifi and ethernet. Creates an ad-hoc wifi network no know network is found for easier config of new systems and in new wifi environments.
 * Camera uploads it's IP address along with image for easy finding of the camera on your network
 * Camera can auto-upload both timestamped and fixed filenames (for use as a webcam or with auto archiving systems requiring a fixed image name).
 * Extensively tested with Canons (600D, 700D, 70D). Works with Nikon D7100 but not extensively tested.
 * Ansible provisioning system for raspberry Pis running Arch Linux.

Features in active  development:
 * Time-series scheduler  to auto-set camera exposure and shutter speed settings (useful for shooting 24-hrs a day in fixed but variable lighting situations like plant growth chambers)
 * RAM spooling with sd card fallback to increase SD card life.
 * Auto Bulb-ramping... we are aspiring to be able to shoot 24-7 DSLR timelapses through sunrise and sunset.
 * DB-based camera management system.
 * Weatherproof and solar powered camera housing  for full remote timelapse solution 
 
To get into the web interface using the wifi the default ip address is 192.168.12.1 and you will need to use the user admin:spceyepidefaultaccess

Code automatically detects connected usb DSLRs and the raspberry pi camera (just duplicate "example.ini" to "picam.ini" and "eyepi.ini"). DSLR's are given a unique ID based on hardware serial. Camera name can be easily changed to a user friendly value.

This is also the pulling repo for the spc-eyepi component of SPC-OS (Arch Linux, the definition is maintained by the Ansible play).

''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

**Requirements**


*base:*
 * python3
 * pysftp<sup>[pip](https://pypi.python.org/pypi/pysftp)</sup>
 * flask
 * flask-bcrypt<sup>[pip](https://pypi.python.org/pypi/Flask-Bcrypt)</sup>
 * pycrypto<sup>[pip](https://pypi.python.org/pypi/pycrypto)</sup>
 * pyudev<sup>[pip](https://pypi.python.org/pypi/pyudev)</sup>
 * pillow<sup>[pip](https://pypi.python.org/pypi/Pillow/3.1.1)</sup>
 * browsepy<sup>[pip](https://pypi.python.org/pypi/browsepy/0.4.0)</sup>
 * gphoto2-cffi<sup>[git](https://github.com/borevitzlab/gphoto2-cffi)</sup>
 
 

*os extras:*
 * create_ap<sup>[aur](https://aur.archlinux.org/packages/create_ap)</sup>
 * [tor](https://www.torproject.org/)
