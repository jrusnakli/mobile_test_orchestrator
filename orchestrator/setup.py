#!/usr/bin/env python3
import setuptools

setuptools.setup(
    name='androidtestorchestrator',
    version='1.1.3',
    package_dir={'': 'src'},
    packages=setuptools.find_packages('src'),
    include_package_data=True,
    entry_points={
    'console_scripts': [
    ]
  }
)
