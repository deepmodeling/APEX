import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="apex-flow",
    version="1.2.0",
    author="Zhuoyuan Li, Tongqi Wen",
    author_email="zhuoyli@outlook.com",
    description="Alloy Properties EXplorer using simulations",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/deepmodeling/APEX.git",
    packages=setuptools.find_packages(),
    install_requires=[
        "pydflow==1.8.54",
        "pymatgen==2024.3.1",
        'pymatgen-analysis-defects==2024.2.24',
        "dpdata==0.2.17",
        "dpdispatcher==0.6.4",
        "phonopy==2.21.2",
        "seekpath==2.1.0",
        "fpop==0.0.7",
        "plotly",
        "dash",
        "dash_bootstrap_components",
        "boto3",
        "pymongo"
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
    entry_points={'console_scripts': [
         'apex = apex.__main__:main',
     ]}
)
