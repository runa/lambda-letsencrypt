import textwrap


class Colors:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    QUESTION = '\033[96m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def write_str(string):
    lines = textwrap.wrap(textwrap.dedent(string), 80)
    for line in lines:
        print(line)


def print_header(string):
    print()
    print(Colors.OKGREEN, end='')
    write_str(string)
    print(Colors.ENDC, end='')


def get_input(prompt, allow_empty=True):
    from sys import version_info
    py3 = version_info[0] > 2  # creates boolean value for test that Python major version > 2
    response = None
    while response is None or (not allow_empty and len(response) == 0):
        print(Colors.QUESTION + "> " + prompt + Colors.ENDC, end='')
        if py3:
            response = input()
        else:
            response = raw_input()
    return response


def get_yn(prompt, default=True):
    if default is True:
        prompt += "[Y/n]? "
        default = True
    else:
        prompt += "[y/N]? "
        default = False
    ret = get_input(prompt, allow_empty=True)
    if len(ret) == 0:
        return default
    if ret.lower() == "y" or ret.lower() == "yes":
        return True
    return False


def get_selection(prompt, options, prompt_after='Please select from the list above', allow_empty=False):
    if allow_empty:
        prompt_after += "(Empty for none)"
    prompt_after += ": "
    while True:
        print(prompt)
        for item in options:
            print('[{}] {}'.format(item['selector'], item['prompt']))
        print()
        choice = get_input(prompt_after, allow_empty=True)

        # Allow for empty things if desired
        if len(choice) == 0 and allow_empty:
            return None

        # find and return their choice
        for x in options:
            if choice == str(x['selector']):
                return x['return']
        print(Colors.WARNING + 'Please enter a valid choice!' + Colors.ENDC)