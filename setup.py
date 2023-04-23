import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="APEX",
    version="0.0.1",
    author="Zhuoyuan Li, Tongqi Wen",
    author_email="zhuoyli@outlook.com",
    description="Alloy Properties EXplorer using simulations",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/deepmodeling/APEX.git",
    packages=setuptools.find_packages(),
    install_requires=[
        "pydflow>=1.6.27",
        "pymatgen>=2022.11.1",
        'pymatgen-analysis-defects',
        "lbg>=1.2.13",
        "dpdata>=0.2.13",
        "matplotlib",
        "seekpath",
        "fpop>=0.0.7",
        "ase"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
    script=[],
    entry_points={'console_scripts': [
         'apex = apex.__main__:main',
     ]}
)
