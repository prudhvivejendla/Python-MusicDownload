import os
import sys
import DownloadFile
import CleanMetadata

DEFAULT_DOWNLOAD_PATH = r"Z:\TElugu wap"

def main():
    print("=" * 60)
    print(" TeluguWap Highest Quality Music Downloader ")
    print("=" * 60)
    
    # Run batch download into the Music directory
    download_dir = DownloadFile.DEFAULT_DOWNLOAD_PATH
    DownloadFile.download_file(output_dir=DEFAULT_DOWNLOAD_PATH)

    # Clean audio metadata after download
    print("\n" + "=" * 60)
    print(" Cleaning Audio Metadata ")
    print("=" * 60)
    CleanMetadata.clean_audio_metadata_from_root(root_path=DEFAULT_DOWNLOAD_PATH)


if __name__ == '__main__':
    main()
