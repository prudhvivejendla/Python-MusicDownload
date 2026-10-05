import urllib.request
import requests
import string
import re
from bs4 import BeautifulSoup
from pathlib import Path

fileExt = [".mp3", ".m4a", "flac"]

indexing = list(string.ascii_uppercase)
indexing.insert(0,"09")
rootPage = "https://teluguwap.net/"
downloadPath = "smb://Prudhvi@192.168.2.48/Songs/"
downloadPath2 = " "
headers = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
}

"""
https://teluguwap.in/load/FLAC/T/Tadakha-(2013)/05_-_Ganagana.flac
https://teluguwap.in/load/Singles/Toxic-(2026)-Singles/01_-_Introducing_Raya_-_Daddy_is_Home_(From_Toxic_-_A_Fairy_Tale_for_Grown-Ups).m4a
https://teluguwap.in/load/Singles/Toxic-(2026)-Singles//.320Kbps/01_-_Introducing_Raya_-_Daddy_is_Home_(From_Toxic_-_A_Fairy_Tale_for_Grown-Ups).mp3
https://cdn.teluguwap.in/load/ALAC/0-9/1-2-3-From-Amalapuram-(2005)-ALAC/05_-_Chandamama_Chira_Katti.m4a
<form action="./files.php" method="GET">

        <input type="hidden" name="type" value="uid">
        <input type="hidden" name="q" value="azwr8">
        <input type="hidden" name="ext" value="m4a">
        <input type="hidden" name="qlty" value="orig-cloud">

        <img src="./inc/img/m4a.png" width="25" style="vertical-align:middle;margin-right:5px;">

        <button type="submit" class="zip-main-btn">
            Apple Lossless [ALAC] [Cloud-DL]
        </button>

        (58.56 MB)

    </form>
"""

def download_file():
    print("Downloading file...")
    print(indexing)
    for index in indexing:
        print("Iterating" , index)
        albums = []
        max_page_no = 1
        response = requests.get(rootPage + index, headers=headers)
        if response.status_code == 200:
            # print(response.text)
            soup = BeautifulSoup(response.content, "html.parser")
            for link in soup.findAll('a'):
                if link.get('href').startswith(index+'-'):
                    current_page_no = int(link.get('href').strip(index+'-'))
                    if current_page_no > max_page_no:
                        max_page_no = current_page_no

        current_page_no = 1
        print('max_page', max_page_no)

        while (current_page_no <= max_page_no):
            current_page_lnk = rootPage + index + '-' +  str(current_page_no)
            print("requesting path", current_page_lnk)
            current_page_no += 1

            response = requests.get(current_page_lnk, headers=headers)
            if response.status_code == 200:
                #print(response.text)
                soup = BeautifulSoup(response.content, "html.parser")
                for link in soup.findAll('a'):
                    album_lnk = link.get('href')
                    if album_lnk.startswith('http') and album_lnk not in albums and re.search(r'[12]\d{3}', album_lnk):
                        albums.append(album_lnk)

                # print(albums)
                for album in albums:
                    songs_lnk = []
                    album_year = re.search(r'(\d{4})', album).group(0)
                    album_title = soup.select('h1')[0].text.strip()
                    album_title = album_title.strip(' Telugu Music')
                    album_title += ' (' + str(album_year) + ')'
                    # print('album_title:', album_title)
                    Path(downloadPath2+album_title).mkdir(parents=True, exist_ok=True)
                    songs_count = 0
                    current_album_page_no = 1
                    while songs_count == len(songs_lnk):
                        songs_count = len(songs_lnk)
                        album_lnk = album + '-' + str(current_album_page_no)
                        response = requests.get(album_lnk, headers=headers)
                        soup = BeautifulSoup(response.content, "html.parser")
                        for link in soup.select('td a'):
                            if link.get('href').startswith('http') and link.get('href') != album and link not in songs_lnk:
                                songs_lnk.append(link.get('href'))
                        current_album_page_no += 1

                    print('album link:', album)
                    print("songs_lnk:", songs_lnk)
                    print("songs count:", len(songs_lnk))

                    for song_link in songs_lnk:
                        response = requests.get(song_link, headers=headers)
                        #print(response.text)
                        if 'load/ALAC/0-9/1-2-3-From-Amalapuram-(2005)-ALAC/05_-_Chandamama_Chira_Katti.m4a' in response.text:
                            print('found song link')
                        else:
                            print('not found song link')

                        # soup = BeautifulSoup(response.content, "html.parser")
                        # song = soup.se

                    break
                break
        break



def download_song(path, link):
    print("Downloading song..." + path)
    response = requests.get(link, headers=headers)
    urllib.request.urlretrieve("http://www.example.com/songs/mp3.mp3", "mp3.mp3")
