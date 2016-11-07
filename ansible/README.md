# Requirements

You need `ansible` version 2.3 installed locally

    sudo pip install git+https://github.com/ansible/ansible

Make add your ssh key to the `keys` folder (you can probably delete ours from there, unless you really want to trust us).

Make sure that your you have an entry in `userlist.yml` and that it has a reference to your ssh key if you want a local user account.

You need to add your ssh key to the list of keys for the user "alarm". After the first login this playbook disables password login for that account for security reasons, and it doesnt check to see whether an ssh key has been added, so it could lock you out.

If you want the raspberry pi to connect to an openvpn server, add your openvpn profile in to the `secure` directory (`ansible/secure/my_vpn_conf.conf`) and set the `vpn_conf` variable in `eyepi.yml` and it will copy and activate/enable that profile.

If you have an api key for traitcapture.org, put it in secure/api_key.yml in this format:
```
api_key:
  "{{your_api_key}}"
```

## SPC-Eyepi

- Build with latest Archlinux Arm (see [this](https://archlinuxarm.org/platforms/armv8/broadcom/raspberry-pi-3#installation) installation guide
- log into the raspberry pi and install python2: `pacman -Syy python2`
- get ip address of your raspberry pi and create an entry in the `hosts` file.
- Run `ansible-playbook -i hosts eyepi.yml`
