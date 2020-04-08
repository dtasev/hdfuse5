import setuptools

with open("README", "r") as fh:
    long_description = fh.read()

setuptools.setup(name="hdfuse",
                 version="1.0.2",
                 author="Dimitar Tasev",
                 author_email="dimtasev@gmail.com",
                 description="hdfuse is a tool for quick inspection of HDF5 files",
                 long_description=long_description,
                 long_description_content_type="text/markdown",
                 url="https://github.com/dtasev/hdfuse5",
                 packages=setuptools.find_packages(),
                 classifiers=[
                     "Programming Language :: Python :: 3.6",
                     "Operating System :: Unix",
                 ],
                 python_requires='>=3.6',
                 entry_points={"console_scripts": ["hdfuse = hdfuse.hdfuse:main"]},
                 install_requires=["fusepy", "h5py"])
