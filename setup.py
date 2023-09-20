import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="APEX",
    version="1.0.0",
    author="Zhuoyuan Li, Tongqi Wen",
    author_email="zhuoyli@outlook.com",
    description="Alloy Properties EXplorer using simulations",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/deepmodeling/APEX.git",
    packages=setuptools.find_packages(),
    install_requires=[
        "pydflow>=1.7.83",
        "pymatgen>=2023.8.10",
        'pymatgen-analysis-defects>=2023.8.22',
        "dpdata>=0.2.13",
        "dpdispatcher",
        "matplotlib",
        "seekpath",
        "fpop>=0.0.7",
        "boto3"
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
