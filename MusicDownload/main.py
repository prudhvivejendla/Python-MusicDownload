import os
import sys
import DownloadFile


def main():
    print("=" * 60)
    print(" TeluguWap Highest Quality Music Downloader ")
    print("=" * 60)
    
    # Run batch download into the Music directory
    download_dir = DownloadFile.DEFAULT_DOWNLOAD_PATH
    DownloadFile.download_file(output_dir=download_dir)


if __name__ == '__main__':
    main()
