from dflow.python import upload_packages
upload_packages.append(__file__)
# constants define
MaxLength = 70

def sepline(ch="-", sp="-", screen=False):
    r"""
    seperate the output by '-'
    """
    ch.center(MaxLength, sp)
