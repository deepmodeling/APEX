import os
__version__ = '1.2.15'
LOCAL_PATH = os.getcwd()


def header():
    header_str = ""
    header_str += "---------------------------------------------------------------\n"
    header_str += "░░░░░░█▐▓▓░████▄▄▄█▀▄▓▓▓▌█░░░░░░░░░░█▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀█░░░░░\n"
    header_str += "░░░░░▄█▌▀▄▓▓▄▄▄▄▀▀▀▄▓▓▓▓▓▌█░░░░░░░░░█░░░░░░░░▓░░▓░░░░░░░░█░░░░░\n"
    header_str += "░░░▄█▀▀▄▓█▓▓▓▓▓▓▓▓▓▓▓▓▀░▓▌█░░░░░░░░░█░░░▓░░░░░░░░░▄▄░▓░░░█░▄▄░░\n"
    header_str += "░░█▀▄▓▓▓███▓▓▓███▓▓▓▄░░▄▓▐██░░░▄▀▀▄▄█░░░░░░░▓░░░░█░░▀▄▄▄▄▄▀░░█░\n"
    header_str += "░█▌▓▓▓▀▀▓▓▓▓███▓▓▓▓▓▓▓▄▀▓▓▐█░░░█░░░░█░░░░░░░░░░░░█░░░░░░░░░░░█░\n"
    header_str += "▐█▐██▐░▄▓▓▓▓▓▀▄░▀▓▓▓▓▓▓▓▓▓▌█░░░░▀▀▄▄█░░░░░▓░░░▓░█░░░█▒░░░░█▒░░█\n"
    header_str += "█▌███▓▓▓▓▓▓▓▓▐░░▄▓▓███▓▓▓▄▀▐█░░░░░░░█░░▓░░░░▓░░░█░░░░░░░▀░░░░░█\n"
    header_str += "█▐█▓▀░░▀▓▓▓▓▓▓▓▓▓██████▓▓▓▓▐█░░░░░▄▄█░░░░▓░░░░░░░█░░█▄▄█▄▄█░░█░\n"
    header_str += "▌▓▄▌▀░▀░▐▀█▄▓▓██████████▓▓▓▌██░░░█░░░█▄▄▄▄▄▄▄▄▄▄█░█▄▄▄▄▄▄▄▄▄█░░\n"
    header_str += "▌▓▓▓▄▄▀▀▓▓▓▀▓▓▓▓▓▓▓▓█▓█▓█▓▓▌██░░░█▄▄█░░█▄▄█░░░░░░█▄▄█░░█▄▄█░░░░\n"
    header_str += "█▐▓▓▓▓▓▓▄▄▄▓▓▓▓▓▓█▓█▓█▓█▓▓▓▐█░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░\n"
    header_str += "---------------------------------------------------------------\n"
    header_str += "        AAAAA         PPPPPPPPP     EEEEEEEEEE  XXX       XXX\n"
    header_str += "       AAA AAA        PPP     PPP   EEE           XXX   XXX\n"
    header_str += "      AAA   AAA       PPP     PPP   EEE            XXX XXX\n"
    header_str += "     AAAAAAAAAAA      PPPPPPPPP     EEEEEEEEE       XXXXX\n"
    header_str += "    AAA       AAA     PPP           EEE            XXX XXX\n"
    header_str += "   AAA         AAA    PPP           EEE           XXX   XXX\n"
    header_str += "  AAA           AAA   PPP           EEEEEEEEEE  XXX       XXX\n"
    header_str += "---------------------------------------------------------------\n"
    header_str += f"==>> Alloy Property EXplorer using simulations (v{__version__})\n"
    header_str += "Please cite DOI: 10.48550/arXiv.2404.17330\n"
    header_str += "Li et al, An extendable cloud-native alloy property explorer (2024).\n"
    header_str += "See https://github.com/deepmodeling/APEX for more information.\n"
    header_str += "---------------------------------------------------------------\n"
    header_str += "Checking input files..."
    print(header_str)
