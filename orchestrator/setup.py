#!/usr/bin/env python3
import setuptools

setuptools.setup(
    name='androidtestorchestrator',
    version='1.0.0',
    package_dir={'': 'src'},
    packages=setuptools.find_packages('src'),
    include_package_data=True
)
