# -*- coding: utf-8 -*-

"""
/***************************************************************************
ViewshedAnalysis
A QGIS plugin
begin : 2013-05-22
copyright : (C) 2013 by Zoran Čučković
email : /
***************************************************************************/

/***************************************************************************
* *
* This program is free software; you can redistribute it and/or modify *
* it under the terms of the GNU General Public License as published by *
* the Free Software Foundation version 2 of the License, or *
* any later version. *
* *
***************************************************************************/
"""

from PyQt5.QtCore import QCoreApplication
from qgis.core import *
try:
    from osgeo import gdal
except ImportError:
    import gdal

import numpy as np

from os import path

from . import visibility as ws

#buffer modes
SINGLE = 0
ADD = 1
MIN = 2
MAX = 3


"""
This class handles input and output of raster data.
It doesn't do any calculations besides combining analysed patches. 
"""
class Raster:

    
    def __init__(self, raster, output=None, crs=None):
	
		
        gdal_raster=gdal.Open(raster)

       
        self.crs = crs if crs else gdal_raster.GetProjection()

        
        
        self.rst = gdal_raster #for speed, keep open raster ?
                        
        # size is y first, like numpy
        self.size = (gdal_raster.RasterYSize, gdal_raster.RasterXSize)
           
    
        #adfGeoTransform[0] /* top left x */
        #adfGeoTransform[1] /* w-e pixel resolution */
        #adfGeoTransform[2] /* rotation, 0 if image is "north up" */
        #adfGeoTransform[3] /* top left y */
        #adfGeoTransform[4] /* rotation, 0 if image is "north up" */
        #adfGeoTransform[5] /* n-s pixel resolution */

        gt=gdal_raster.GetGeoTransform()
        self.pix = gt[1] 
                
        raster_x_min = gt[0]
        raster_y_max = gt[3] # it's top left y, so maximum!

        raster_y_min = raster_y_max - self.size[0] * self.pix
        raster_x_max = raster_x_min + self.size[1] * self.pix

        
##        xsize = gdalData.RasterXSize 
##        ysize = gdalData.RasterYSize 
        
        self.extent = [raster_x_min, raster_y_min, 
                       raster_x_max, raster_y_max]

        self.min, self.max = gdal_raster.GetRasterBand(1
                            ).GetStatistics(True, True)[:2]

         # Get raster statistics
##        srcband = gdalData.GetRasterBand(1)
##
##
##        raster_max= srcband.GetMaximum()
##        raster_min = srcband.GetMinimum()
##
##        nodata = srcband.GetNoDataValue()
##
##        data_type =  srcband.DataType

        """
        NP2GDAL_CONVERSION = {
          "uint8": 1,
          "int8": 1,
          "uint16": 2,
          "int16": 3,
          "uint32": 4,
          "int32": 5,
          "float32": 6,
          "float64": 7,
          "complex64": 10,
          "complex128": 11,
        }

        """

        self.output = output

    def pixel_coords (self, x, y):
        
        x_min = self.extent[0]; y_max = self.extent[3]
        return (int((x - x_min) / self.pix),
                int((y_max - y) / self.pix)) #reversed !
    

     
    def set_master_window(self, radius,
                          size_factor=1,
                          curvature=False,
                          refraction=0,
                          background_value=0,
                          pad=False):
        """
        Prepares the DEM array and (optionally) padded view for fast window extraction.
        
        Parameters:
          radius           – search radius in map units
          size_factor      – (unused here; for compatibility)
          curvature        – if True, precompute earth‐curvature correction matrix
          refraction       – refraction coefficient for curvature calculation
          background_value – value to pad edges (and replace NoData)
          pad              – if True, build a padded DEM for O(1) window slicing
        """
        # 1) Compute pixel radius and store
        self.radius = radius
        self.radius_pix = int(radius / self.pix)
        r = self.radius_pix

        # 2) Load full DEM into numpy array and mask out NoData
        band = self.rst.GetRasterBand(1)
        self.array = band.ReadAsArray().astype(float)
        nodata = band.GetNoDataValue()
        if nodata is not None:
            self.array[self.array == nodata] = background_value

        # 3) Initialize one reusable window buffer
        win_size = 2 * r + 1
        self.window = np.empty((win_size, win_size), dtype=self.array.dtype)

        # 4) If padding enabled, pad once for all future windows
        if pad:
            self._padded_dem = np.pad(
                self.array,
                pad_width=r,
                mode='constant',
                constant_values=background_value
            )
        else:
            self._padded_dem = None

        # 5) (Optional) Precompute curvature matrix
        if curvature:
            self.curvature = self.curvature_matrix(refraction)

        # 6) Always compute angular matrix and max‐distance for masking
        self.angles = self.angular_matrix()
        self.mx_dist = self.distance_matrix(squared=False)


        
    """
    Create the output file in memory and determine the mode of combining results
    (addition or min/max)

    If live_memory is True, a buffer will be the same size as the entire raster,
    otherwise it will have the size of master window. The latter approach is 15 - 20% slower. 
    
    """
    def set_buffer (self, mode = ADD, live_memory = False):

        self.fill =0 if mode == ADD else np.nan

        self.mode = mode 

        if live_memory:

            self.result = np.zeros(self.size)
            if mode != ADD : self.result [:] = np.nan
                        
        else: self.result = None           
        

    """
    Name is self-explanatory... Divide with pixel size if needed.
    Diameter is expressed as half of the major axis plus half of the minor:
    this should work best for moderate latitudes.
    """

    """
    Model vertical drop from a plane to spherical surface (of the Earth)
    Note that it has to be multiplied with pixel size to get usable values

    """
    def get_curvature_earth (self):

        crs= self.crs		
    
        start = crs.find("SPHEROID") + len("SPHEROID[")
        end = crs.find("]],", start) + 1
        tmp = crs[start:end].split(",")

        try:
                semiMajor = float(tmp[1])
                if not 6000000 < semiMajor < 7000000:
                        semiMajor = 6378137
        except:
                semiMajor = 6378137

        try:
                flattening = float(tmp[2])
                if not 296 < flattening < 301:
                        flattening = 298.257223563
        except:
                flattening = 298.257223563

        semiMinor = semiMajor - semiMajor / flattening
        
        return semiMajor + semiMinor

        
    def curvature_matrix(self, refraction=0):
        #see https://www.usna.edu/Users/oceano/pguth/md_help/html/demb30q0.htm
    
        dist_squared = self.distance_matrix(squared=True)
        # all distances are in pixels in doViewshed module !!
        # formula is  squared distance / diam_earth 
        # need to divide all with pixel size (squared !!)
        D = self.get_curvature_earth() / (self.pix **2)
            
        return (dist_squared / D) * (1 - refraction) 

 
    """
    Calculate a mask (can be set for each point)
    """
    def set_mask (self,
                  radius_out,
                  radius_in=None,
                  azimuth_1=None,
                  azimuth_2=None ):

        #angular mask is set in visibility routine : it needs angular data, curvature etc. 


        mask = self.mx_dist < radius_out

        if radius_in : mask *= self.mx_dist > radius_in 

        if azimuth_1 != None and azimuth_2 != None:

            operator = np.logical_and if azimuth_1 < azimuth_2 else np.logical_or

            mask_az = operator(self.angles > azimuth_1, self.angles < azimuth_2)

            mask *= mask_az
    
        self.mask = ~ mask

    """
    Return a map of distances from the central pixel.
    Attention: these are pixel distances, not geographical !
    (to convert to geographical distances: multiply with pixel size)
    """
    def distance_matrix (self, squared=False):

        r = self.radius_pix
        window = self.window.shape[0]
        
        temp_x= ((np.arange(window) - r) ) **2
        temp_y= ((np.arange(window) - r) ) **2

        if not squared:
            return np.sqrt(temp_x[:,None] + temp_y[None,:])
        # squared values
        else: return temp_x[:,None] + temp_y[None,:]


    def angular_matrix (self):
        r = self.radius_pix
        window = self.window.shape[0]

        temp_x= np.arange(window)[::-1] - r
        temp_y= np.arange(window) - r

        angles=np.arctan2(temp_y[None,:], temp_x[:,None]) * 180 / np.pi

        angles[angles<0] += 360

        return angles


    def open_window(self, pixel_coord):
        """
        Extracts a square window of size (2*radius_pix+1) around pixel_coord.

        If padding was enabled in set_master_window, this is a single NumPy slice
        into the padded DEM (fast path); otherwise it falls back to safe edge‐checked slicing.
        """
        x_pix, y_pix = pixel_coord
        r = self.radius_pix

        if self._padded_dem is not None:
            # Fast path: grab the block and copy it into the reusable buffer
            block = self._padded_dem[
                y_pix : y_pix + 2*r + 1,
                x_pix : x_pix + 2*r + 1
            ]
            np.copyto(self.window, block)

        else:
            # Safe path: handle edges by clamping and filling background (0)
            h, w = self.array.shape
            x0 = max(0, x_pix - r)
            x1 = min(w, x_pix + r + 1)
            y0 = max(0, y_pix - r)
            y1 = min(h, y_pix + r + 1)

            # fill with background
            self.window.fill(0)

            # compute where the valid subarray goes in the window
            win_x0 = x0 - (x_pix - r)
            win_y0 = y0 - (y_pix - r)

            sub = self.array[y0:y1, x0:x1]
            self.window[
                win_y0 : win_y0 + sub.shape[0],
                win_x0 : win_x0 + sub.shape[1]
            ] = sub

        return self.window

        if isinstance(self.curvature, np.ndarray):
            
            self.window[
                slice(*in_slice_y), slice(*in_slice_x)] -= self.curvature[
                slice(*in_slice_y), slice(*in_slice_x)]
        # there is a problem with interpolation:
        # when the analysis window stretches outside raster borders
        # the last row/column will be interpolated with the fill value
        # the solution is to copy the same values or to catch these vaules (eg. by initialising to np.nan)
        if self.pad:
            if x_offset_dist_mx:
                self.window[:,in_slice_x[0] -1] = self.window[:,in_slice_x[0]]
            # slice[:4] will give indices 0 to 3, so we need -1 to get the last index!
            if x + rx + 1 > self.size[1]:
                self.window[:,in_slice_x[1] ] =  self.window[:,in_slice_x[1] -1 ]

            if y_offset_dist_mx:
                self.window[in_slice_y[0] -1,:] = self.window[in_slice_y[0],:]

            if y + rx + 1 > self.size[0]:
                self.window[in_slice_y[1] , : ] = self.window[in_slice_y[1] -1, : ]
        
##        self.offset = (x_offset, y_offset)
##        self.win_offset= (x_offset_dist_mx, y_offset_dist_mx)
##        self.win_size = (window_size_x, window_size_y)

    """
    reads entire raster
    """
    def open_raster (self):
        self.raster = self.rst.ReadAsArray().astype(float)
        return self.raster
        
    """
    Insert a numpy matrix to the same place where data has been extracted.
    Data can be added-up or chosen from highest/lowest values.
    All parameteres are copied from class properties
    because only one window is possible at a time.
    """
    def add_to_buffer(self, in_array, report = False):

        try: in_array[self.mask] = self.fill
        except: pass #an array may be unmasked 

        y_in = slice(*self.inside_window_slice[0])
        x_in = slice(*self.inside_window_slice[1])

        m_in = in_array [y_in, x_in]
  

        if isinstance(self.result, np.ndarray):
            m = self.result[self.window_slice]
        else :
            m = self.gdal_output.ReadAsArray(*self.gdal_slice).astype(float)
    
        if self.mode == SINGLE: m = m_in
        
        elif self.mode == ADD:  m += m_in

        else:
            flt = m_in < m if self.mode == MIN else m_in > m
            
        #there is a problem to initialise a comparison without knowing min/max values
##            # nan will always give False in any comarison
##            # so make a trick with isnan()...
            flt[np.isnan(m)]= True

            m[flt]= m_in[flt]
        
        if not isinstance(self.result, np.ndarray): #write to file
            
            bd = self.gdal_output.GetRasterBand(1)
            #for writing gdal takes only x and y offset (1st 2 values of self.gdal_slice)
            bd.WriteArray( m, *self.gdal_slice[:2] )
    
            bd.FlushCache()
            
            
            #np.where(self.result [self.window_slice] < in_array [self.inside_window_slice],
            #         in_array [self.inside_window_slice], self.result [self.window_slice])      
            

        if report:
            try:
                if self.fill != 0 :  m_in[self.mask[y_in, x_in]]=0
                c = np.count_nonzero(m_in)
                
                 # Count values outside mask (mask is True on the outside !)
                total = m_in.size - np.count_nonzero(self.mask[y_in, x_in])

                return ( c ,  total )

            except: #unmasked array
                return (np.count_nonzero(m_in), m_in.size) 
    
    
    """
    Writing analysis result.
     - If there is no result assigned to the class, it will produce an empty file.
     - If there is no file name, it will write the result to previously created file. 
       
    """
    def write_output (self, file_name=None,
                     no_data = np.nan,
                     dataFormat = gdal.GDT_Float32, 
                     compression = True):

        if file_name:  # create a file
                                
            driver = gdal.GetDriverByName('GTiff')
            ds = driver.Create(file_name, self.size[1], self.size[0], 1, 
                               dataFormat, ['COMPRESS=LZW' if compression else ''])
            ds.SetProjection(self.crs)
            ds.SetGeoTransform(self.rst.GetGeoTransform())

            ds.GetRasterBand(1).SetNoDataValue(no_data)           
            ds.GetRasterBand(1).Fill(self.fill)
            ds.FlushCache() #important, otherwise we need to delete ds, to force the flush!

            # for buffered operations (...hacky ...)
            self.gdal_output = ds           
      
        else:
            ds = self.gdal_output

        try:
            ds.GetRasterBand(1).WriteArray(self.result )
            ds = None
        except: pass
    

       

	  

    
        

