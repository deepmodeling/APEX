import os
__version__ = '1.2.0.nightly-2024-04-28'
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
    header_str += "Checking input files..."
    print(header_str)
