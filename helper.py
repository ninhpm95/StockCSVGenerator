import os
from typing import Literal
from constants import FILE_NAME

RegionType = Literal['JP', 'US', 'Unknown']

def get_region(filename: str = FILE_NAME) -> RegionType:
    """
    Determines the market region based on the filename.
    
    Args:
        filename: The name or path of the file (defaults to FILE_NAME).
        
    Returns:
        'JP', 'US', or 'Unknown'
    }
    """
    # Extract just the filename in case a full path is passed
    base_name = os.path.basename(filename).upper()

    if 'JP' in base_name:
        return 'JP'
    if 'US' in base_name:
        return 'US'
        
    return 'Unknown'
