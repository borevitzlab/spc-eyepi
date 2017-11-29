
Ansible Deployment
##################

Requirements
------------

.. highlight:: console

You need `Ansible <https://www.ansible.com/>`_ version 2.3 installed locally

.. code-block:: bash

    # pip install git+https://github.com/ansible/ansible

You must also have the sshpass program (according to ansible), this is ussed only for first setup of ssh keyfile access.

if you are using ArchLinux

.. code-block:: bash

    # pacman -S sshpass

or if you are using Ubuntu/Debian

.. code-block:: bash

    # apt-get install sshpass


Setup
-----

Add your ssh public key to the *ansible/keys* folder (you can also delete ours from there, unless you **really** trust us).

For a local user account to be made there must be an entry in *ansible/vars/userlist.yml* and with a reference to your ssh key.
You need to add your ssh key to the list of keys for the user "alarm", as this is the user that ansible needs to install things.

Do not remove *clear_password* from the alarm user!


The contents of users file should look something like this:

.. code-block:: yaml
    :caption: *ansible/vars/userlist.yml*
    :name: userlist

    users:
      - name: alarm,  shell: /bin/bash, groups: [wheel, adm, users],
          clear_password: yes,
          keys: [your_key.pub]}
      - {name: your_username,  shell: /bin/bash, groups: [wheel, adm, users],
          keys: [your_key.pub]}

After the first login this playbook locks down authentication (including disabling password logins) and doesn't check to see whether an ssh key has been added, so you could lock yourself out.


OpenVPN
-------
If you want the RPi to be connected to an openvpn server, add your openvpn profile in to the *secure* directory (*ansible/secure/my_vpn_conf.conf*) and set the *vpn_conf* variable in *eyepi.yml* and it will copy and activate/enable that profile.


Setting up the Raspberry Pi
---------------------------

Create an SD card with the latest Archlinux Arm (see the `installation guide <https://archlinuxarm.org/platforms/armv8/broadcom/raspberry-pi-3#installation>`_)

Log into the raspberry pi using ssh with the username:password *alarm:alarm* and install python2.


The RPi doesn't have **sudo** installed yet, so you must use **su**. The root password is *root*:

.. code-block:: bash

    [alarm@alarm ~]$ su
    [root@alarm ~]$ pacman -Syy python2


Add the ip address of the RPi to the list in the *ansible/hosts* file so that it looks like this:

.. code-block:: guess
    :caption: *ansible/hosts*
    :name: hosts

    [all:vars]
    build_gphoto2=False

    [rpis]
    rpi_name ansible_host=your_rpi_ip_address

    [rpis:vars]
    ansible_user=alarm
    ansible_password=alarm
    become_user=root
    ansible_become=yes
    ansible_become_method=su
    ansible_become_pass=root
    ansible_python_interpreter=/usr/bin/python2


Traitcapture.org integration
----------------------------

If you have an api key for traitcapture.org you can put it in the [all:vars] section of *hosts*

.. code-block:: guess
    :caption: *ansible/hosts*
    :name: hosts

    [all:vars]
    api_key=eyJhbGciOiJIUzI1NiIsImV4cCI6MTUxMTkzNTA3MywiaWF0IjoxNTExOTMxNDczfQ.eyJpZCI6IjU1ODdiZDhjZDEzMTQ0MjNiN2FhYzk0NyJ9.IauJ-suCv60iCGxKe4S6XYSnNT5WYHNHZ1azyMbfzSw
    build_gphoto2=False

    [rpis]
    ...


Extra Options
-------------

You can opt to build gphoto2 and libgphoto2 by setting the *build_gphoto2* to True in the [all:vars] section


Running the play
----------------

To run the play

.. code-block:: bash

    $ ansible-playbook -i hosts eyepi.yml

You can use the same command to update the software on the RPi if it has the same ip address.


