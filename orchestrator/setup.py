#!/usr/bin/env python3
import setuptools

setuptools.setup(
    name='mobiletestorchestrator',
    version='2.1.0',
    package_dir={'': 'src'},
    packages=setuptools.find_packages('src'),
    include_package_data=True,
    entry_points={
        'console_scripts': []
    },
    install_requires=['apk-bitminer>=1.1.0'],

)
