# /usr/share/ansible/plugins/filter/format_list.py (check filter_plugins path in ansible.cfg)

def format_list(list_, pattern):
    return [pattern.format(s) for s in list_]

class FilterModule(object):
    def filters(self):
        return {
            'format_list': format_list,
        }
