
class ICloudPhotosLibrary:
    def __init__(self, icloud, logger):
        self.icloud = icloud
        self.logger = logger


    def get_top_level_albums(self):
        albums_dict = self.__get_albums_or_die()
        albums = albums_dict.values()  # pragma: no cover

        # TODO: No exception handler set?
        return albums


    def find_album(self, album_name):
        self.logger.debug(
            "Looking up all photos%s from album %s...",
            "" if skip_videos else " and videos",
            album_name)

        album = self.__get_albums_or_die()[album_name]

        album.exception_handler = self.__build_photos_exception_handler()

        return album


    def collect_sub_albums(self, album):
        def _fetch_folders():
            url = ('%s/records/query?%s' %
                (self.icloud.photos._service_endpoint, urlencode(self.icloud.photos.params)))
            json_data = json.dumps({
                "query": {"recordType": "CPLAlbumByPositionLive", "filterBy": album.query_filter },
                "zoneID": {"zoneName": "PrimarySync"}})

            request = self.icloud.photos.session.post(
                url,
                data=json_data,
                headers={'Content-type': 'text/plain'}
            )
            response = request.json()

            return response['records']

        def _to_album(folder):
            if folder['recordName'] in ('----Root-Folder----',
                                        '----Project-Root-Folder----') or \
                    (folder['fields'].get('isDeleted') and
                    folder['fields']['isDeleted']['value']):
                return None
            
            folder_id = folder['recordName']
            folder_obj_type = \
                "CPLContainerRelationNotDeletedByAssetDate:%s" % folder_id
            folder_name = base64.b64decode(
                folder['fields']['albumNameEnc']['value']).decode('utf-8')
            query_filter = [{
                "fieldName": "parentId",
                "comparator": "EQUALS",
                "fieldValue": {
                    "type": "STRING",
                    "value": folder_id
                }
            }]
            
            return PhotoAlbum(photos_service, folder_name,
                            'CPLContainerRelationLiveByAssetDate',
                            folder_obj_type, 'ASCENDING', query_filter)


        folders = _fetch_folders()
        sub_albums = []
        for folder in folders:
            sub_albums.append(_to_album(folder))

        return sub_albums
    
    def __get_albums_or_die(self):
        # Default album is "All Photos", so this is the same as
        # calling `icloud.photos.all`.
        # After 6 or 7 runs within 1h Apple blocks the API for some time. In that
        # case exit.
        try:
            return self.icloud_photos_service.albums
        except PyiCloudAPIResponseError as err:
            # For later: come up with a nicer message to the user. For now take the
            # exception text
            print(err)
            sys.exit(1)
    
    def __build_photos_exception_handler(self):
        def photos_exception_handler(ex, retries):
            """Handles session errors in the PhotoAlbum photos iterator"""
            if "Invalid global session" in str(ex):
                if retries > constants.MAX_RETRIES:
                    self.logger.tqdm_write(
                        "iCloud re-authentication failed! Please try again later."
                    )
                    raise ex
                self.logger.tqdm_write(
                    "Session error, re-authenticating...",
                    logging.ERROR)
                if retries > 1:
                    # If the first re-authentication attempt failed,
                    # start waiting a few seconds before retrying in case
                    # there are some issues with the Apple servers
                    time.sleep(constants.WAIT_SECONDS * retries)
                self.icloud.authenticate()

        return photos_exception_handler