from __future__ import division

import struct

import numpy as N

from osgeo import osr
from osgeo import gdal as G
from pyproj import Proj
from fiona.crs import from_string
from shapely.geometry import shape
from shapely.ops import transform

from .proj import Transformation
import strategies

G.UseExceptions()

class open(object):
	"""Context manager for image"""
	def __init__(self, filename):
		self.image = Dataset(filename)
	def __enter__(self):
		return self.image
	def __exit__(self, exception_type, exception_val, trace):
		self.image.close()

class RasterProxy(object):
	from .area import create_mask, extract_area

	@property
	def geomatrix(self):
		return self.__dataset__.__gdal__.GetGeoTransform()

	def pixel_coordinates(self,geometry, snap=False):
		def project_coord(x,y,z=None):
			g = self.geomatrix
			x = ((x - g[0])/g[1])
			y = ((y - g[3])/g[5])
			if snap:
				x, y = [int(i) for i in x,y]
			if z is None:
				return x,y
			else:
				return x,y,z
		return transform(project_coord, geometry)

	def map_coordinates(self,geometry):
		"""
		Takes records projected as pixels and converts them to map coordinates.
		It is presumed that a position within each pixel is represented as a linear range.
		Thus, the center of the top-left pixel maps to (0.5,0.5), and the very corner of
		the image maps to (0,0)
		"""
		g = self.geomatrix
		def project_coord(x,y,z=None):
			x = g[0] + g[1] * x + g[2] * y
			y = g[3] + g[4] * x + g[5] * y
			if z is None:
				return x,y
			else:
				return x,y,z
		return transform(project_coord, geometry)

class Band(RasterProxy):
	"""Wraps a GDAL raster band"""
	def __init__(self, dataset, index=0):
		self.__dataset__ = dataset
		self.index = index
		self.__gdal__ = self.__dataset__.__gdal__.GetRasterBand(index+1)
		self.shape = dataset.shape
		self.nodata = self.__gdal__.GetNoDataValue()

	def get_array(self, nodata=None):
		arr = self.__gdal__.ReadAsArray()
		if nodata is None:
			nodata = self.nodata #try to set gdal nodata value
		if nodata == 'nan':
			return N.ma.array(arr, mask = N.isnan(arr))
		if nodata is not None:
			try:
				arr[arr == nodata] = N.nan
			except ValueError:
				return arr
		return arr # if all else fails

	def get_pixel(self, x,y):
		"""Gets the value of a single pixel for every band"""
		structval=self.__gdal__.ReadRaster(px,py,1,1,buf_type=G.GDT_UInt16) #Assumes 16 bit int aka 'short'
		return struct.unpack('h' , structval)[0]

	def extract(self, geometry,strategy=strategies.nearest):
		"""
		Project to pixel coordinates and extract raster values
		"""
		pixels = self.extract_pixels(self.pixel_coordinates(geometry))
		return self.map_coordinates(pixels)

	def extract_area(self, pixel_geometry):
		""" Returns a list of x,y,value coordinates in a nx3 matrix.
		"""
		area = self.__dataset__.extract_area(pixel_geometry, bands=[self.index])
		return area

	def extract_pixels(self, pixel_geometry, strategy=strategies.nearest):
		"""
		Strategies don't currently mean anything for area geometries
		"""
		if pixel_geometry.area == 0:
			return strategy(self.get_array(),pixel_geometry)
		else:
			return self.extract_area(pixel_geometry)

	dtype = property(lambda self: G.GetDataTypeName(self.__gdal__.DataType))
	__str__ = lambda self: self.__gdal__.GetDescription()
	__len__ = lambda self: self.__gdal__.YSize


class Dataset(RasterProxy):
	"""Wraps a GDAL dataset, might make it more sane."""
	def __init__(self, filename):

		self.__dataset__ = self
		self.__gdal__ = G.Open(filename, G.GA_ReadOnly)
		self.shape = (self.__gdal__.RasterYSize,self.__gdal__.RasterXSize)

		try:
			self.wkt = self.__gdal__.GetProjection()
			self.__osr__ = osr.SpatialReference()
			self.__osr__.ImportFromWkt(self.wkt)
			self.crs = from_string(self.__osr__.ExportToProj4())
			self.gcs = from_string(self.__osr__.CloneGeogCS().ExportToProj4())
			self.projected = True
		except AttributeError:
			self.projected = False

	def get_band(self, index=0):
		return Band(self, index)

	def bands(self):
		pass

	def transformation(self,crs):
		return Transformation(crs,self.crs)

	def profile(self, x,y, bands=None, window=1):
		if bands is None:
			bands = range(len(self))
		bands = [b+1 for b in bands]
		buffer=self.__gdal__.ReadRaster(
			int(x-1//window),
			int(y-1//window),
			window,
			window,
			band_list=bands)
		arr = N.fromstring(buffer, dtype=self.dtype)
		arr = arr.reshape((len(self), window, window))
		arr = N.ma.masked_array(arr, arr==self.nodata)
		arr = arr.mean(axis=1).mean(axis=1)
		return list(arr.filled(N.nan).flatten())

	@property
	def dtype(self):
		return self.get_band().dtype

	@property
	def nodata(self):
		return self.get_band().nodata

	def close(self):
		self.__gdal__ = None

	def __len__(self):
		return self.__gdal__.RasterCount
