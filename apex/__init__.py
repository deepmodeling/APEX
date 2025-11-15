import os
__version__ = '1.2.17'
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
    header_str += "Please cite DOI: 10.1038/s41524-025-01580-y\n"
    header_str += "Li, Z., Wen, T., Zhang, Y. et al. APEX: an automated cloud-native material property explorer. npj Comput Mater 11, 88 (2025).\n"
    header_str += "See https://doi.org/10.1038/s41524-025-01580-y for more information.\n"
    header_str += "---------------------------------------------------------------\n"
    header_str += "Checking input files..."
    print(header_str)
