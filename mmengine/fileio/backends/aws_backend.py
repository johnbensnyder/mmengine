import inspect
import io
import os
import os.path as osp
import re
import tempfile
import warnings
from abc import ABCMeta, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple, Union
from urllib.request import urlopen

from .base import BaseStorageBackend

class AWSBackend(BaseStorageBackend):
    """AWSBackend is Amazon Simple Storage Service(s3).

    AWSBackend supports reading and writing data to aws s3.
    It relies on awscli and boto3, you must install them and run
    ``aws configure`` in advance to use it.

    .. code-block:: shell
        :linenos:

        pip install awscli
        pip install boto3

        # configure your aws credentials
        # Note that region must match your bucket region.
        aws configure
        # AWS Access Key ID [****************JQGK]:
        # AWS Secret Access Key [****************3gUp]:
        # Default region name [ap-east-1]:
        # Default output format [json]:

    Note:
        In v1.4.7 and later, AWSBackend is provided to support reading and
        writing data to s3.

    Args:
        path_mapping (dict, optional): Path mapping dict from local path to
            Petrel path. When ``path_mapping={'src': 'dst'}``, ``src`` in
            ``filepath`` will be replaced by ``dst``. Default: None.

    Examples:
        >>> from mmcv.fileio.file_client import AWSBackend
        >>> filepath = 's3://bucket/obj'
        >>> client = AWSBackend()
        >>> client.get(filepath1)  # get data from aws s3
        >>> client.put(obj, filepath)
    """
    
    def __init__(self, path_mapping: Optional[dict] = None):
        try:
            import boto3
            import boto3.s3.transfer
            import botocore.exceptions
        except ImportError:
            raise ImportError('Please install boto3 to enable AWSBackend '
                              'by "pip install boto3".')

        self._client = boto3.client('s3')
        self.transfer_config = boto3.s3.transfer.TransferConfig(
            use_threads=True)
        assert isinstance(path_mapping, dict) or path_mapping is None
        self.path_mapping = path_mapping
        # Used to parse bucket and obj_name
        self.parse_bucket = re.compile('s3://(.+)')
        self.check_exception = botocore.exceptions.ClientError
    
    def _map_path(self, filepath: Union[str, Path]) -> str:
        """Map ``filepath`` to a string path whose prefix will be replaced by
        :attr:`self.path_mapping`.

        Args:
            filepath (str or Path): Path to be mapped.
        """
        filepath = str(filepath)
        if self.path_mapping is not None:
            for k, v in self.path_mapping.items():
                filepath = filepath.replace(k, v)
        return filepath
    
    def _format_path(self, filepath: str) -> str:
        """Convert a ``filepath`` to standard format of aws s3.

        If the ``filepath`` is concatenated by ``os.path.join``, in a Windows
        environment, the ``filepath`` will be the format of
        's3://bucket_name\\image.jpg'. By invoking :meth:`_format_path`, the
        above ``filepath`` will be converted to 's3://bucket_name/image.jpg'.

        Args:
            filepath (str): Path to be formatted.
        """
        return re.sub(r'\\+', '/', filepath)

    def _parse_path(self, filepath: str) -> Tuple[str, str]:
        """Parse bucket and object name from a given ``filepath``.

        If the ``filepath`` is 's3://bucket/path/object', it will be parsed to
        'bucket' and 'path/object'. If the ``filepath`` is 's3://bucket', it
        will be parsed to 'bucket' and ''. If the ``filepath`` not start with
        's3://', it will thrown exception.

        Args:
            filepath (str or Path): Path to read data.

        Returns:
            tuple[str, str]: The first element is the name of the bucket, and
            the second is the relative path to the bucket.
        """
        filepath = self._map_path(filepath)
        filepath = self._format_path(filepath)
        parse_res = self.parse_bucket.match(filepath)
        if not parse_res:
            raise ValueError(f'filepath {filepath} is not a valid s3 path.')
        path = parse_res.groups()[0]
        path_split = path.split('/', maxsplit=1)
        bucket = path_split[0]
        obj_name = ''
        if len(path_split) > 1:
            obj_name = path_split[1]
        return bucket, obj_name

    def _check_bucket(self, bucket: str) -> bool:
        """Check if bucket exists.

        Args:
            bucket (str): Bucket name

        Returns:
            bool: True if the bucket exists.
        """
        try:
            self._client.head_bucket(Bucket=bucket)
            return True
        except self.check_exception:
            raise self.check_exception(f'Bucket {bucket} is not found.')

    def _check_object(self, bucket: str, obj_name: str) -> bool:
        """Check if object exists.

        Args:
            bucket (str): Bucket name
            obj_name (str): Object name

        Returns:
            bool: True if the object exists.
        """
        try:
            self._client.head_object(Bucket=bucket, Key=obj_name)
            return True
        except self.check_exception:
            raise self.check_exception(
                f'Object s3://{bucket}/{obj_name} is not found.')

    def get(self, filepath: Union[str, Path]) -> memoryview:
        """Read data from a given ``filepath`` with 'rb' mode.

        Args:
            filepath (str or Path): Path to read data.

        Returns:
            memoryview: A memory view of expected bytes object to avoid
            copying. The memoryview object can be converted to bytes by
            ``value_buf.tobytes()``.
        """
        bucket, obj_name = self._parse_path(filepath)
        self._check_object(bucket, obj_name)
        with io.BytesIO() as buff:
            self._client.download_fileobj(
                bucket, obj_name, buff, Config=self.transfer_config)
            value_buf = memoryview(buff.getvalue())
        return value_buf

    def get_text(self,
                 filepath: Union[str, Path],
                 encoding: str = 'utf-8') -> str:
        """Read data from a given ``filepath`` with 'r' mode.

        Args:
            filepath (str or Path): Path to read data.
            encoding (str): The encoding format used to open the ``filepath``.
                Default: 'utf-8'.

        Returns:
            str: Expected text reading from ``filepath``.
        """
        return str(self.get(filepath), encoding=encoding)

    def put(self, obj: bytes, filepath: Union[str, Path]) -> None:
        """Save data to a given ``filepath``.

        Args:
            obj (bytes): Data to be saved.
            filepath (str or Path): Path to write data.
        """
        bucket, obj_name = self._parse_path(filepath)
        self._check_bucket(bucket)
        with io.BytesIO(obj) as buff:
            self._client.upload_fileobj(
                buff, bucket, obj_name, Config=self.transfer_config)

    def put_text(self,
                 obj: str,
                 filepath: Union[str, Path],
                 encoding: str = 'utf-8') -> None:
        """Save data to a given ``filepath``.

        Args:
            obj (str): Data to be written.
            filepath (str or Path): Path to write data.
            encoding (str): The encoding format used to encode the ``obj``.
                Default: 'utf-8'.
        """
        self.put(bytes(obj, encoding=encoding), filepath)

    def remove(self, filepath: Union[str, Path]) -> None:
        """Remove a file from aws s3.

        Args:
            filepath (str or Path): Path to be removed.
        """
        bucket, obj_name = self._parse_path(filepath)
        self._client.delete_object(Bucket=bucket, Key=obj_name)

    def exists(self, filepath: Union[str, Path]) -> bool:
        """Check whether a file path exists.

        Args:
            filepath (str or Path): Path to be checked whether exists.

        Returns:
            bool: Return ``True`` if ``filepath`` exists, ``False`` otherwise.
        """
        bucket, obj_name = self._parse_path(filepath)
        return self._check_object(bucket, obj_name)

    def isdir(self, filepath: Union[str, Path]) -> bool:
        """Check whether a file path is a directory.

        Args:
            filepath (str or Path): Path to be checked whether it is a
                directory.

        Returns:
            bool: Return ``True`` if ``filepath`` points to a directory,
            ``False`` otherwise.
        """
        bucket, obj_name = self._parse_path(filepath)
        if self._check_bucket(bucket) and (obj_name.endswith('/')
                                           or obj_name == ''):
            return True
        return False

    def isfile(self, filepath: Union[str, Path]) -> bool:
        """Check whether a file path is a file.

        Args:
            filepath (str or Path): Path to be checked whether it is a file.

        Returns:
            bool: Return ``True`` if ``filepath`` points to a file, ``False``
                otherwise.
        """
        bucket, obj_name = self._parse_path(filepath)
        if self._check_bucket(
                bucket) and not obj_name.endswith('/') and obj_name != '':
            return True
        return False

    def join_path(self, filepath: Union[str, Path],
                  *filepaths: Union[str, Path]) -> str:
        """Concatenate all file paths.

        Args:
            filepath (str or Path): Path to be concatenated.

        Returns:
            str: The result after concatenation.
        """
        filepath = self._format_path(self._map_path(filepath))
        if filepath.endswith('/'):
            filepath = filepath[:-1]
        formatted_paths = [filepath]
        for path in filepaths:
            formatted_paths.append(self._format_path(self._map_path(path)))
        return '/'.join(formatted_paths)

    @contextmanager
    def get_local_path(self, filepath: Union[str, Path]) -> Iterable[str]:
        """Download a file from ``filepath`` and return a temporary path.

        ``get_local_path`` is decorated by :meth:`contxtlib.contextmanager`. It
        can be called with ``with`` statement, and when exists from the
        ``with`` statement, the temporary path will be released.

        Args:
            filepath (str | Path): Download a file from ``filepath``.

        Examples:
            >>> client = AWSBackend()
            >>> # After existing from the ``with`` clause,
            >>> # the path will be removed
            >>> with client.get_local_path('s3://path/of/your/file') as path:
            ...     # do something here

        Yields:
            Iterable[str]: Only yield one temporary path.
        """
        assert self.isfile(filepath)
        try:
            f = tempfile.NamedTemporaryFile(delete=False)
            f.write(self.get(filepath))
            f.close()
            yield f.name
        finally:
            os.remove(f.name)

    def list_dir_or_file(self,
                         dir_path: Union[str, Path],
                         list_dir: bool = True,
                         list_file: bool = True,
                         suffix: Optional[Union[str, Tuple[str]]] = None,
                         recursive: bool = False) -> Iterator[str]:
        """Scan a directory to find the interested directories or files in
        arbitrary order.

        Note:
            AWS s3 has no concept of directories but it simulates the directory
            hierarchy in the filesystem through public prefixes. In addition,
            if the returned path ends with '/', it means the path is a public
            prefix which is a logical directory.

        Note:
            :meth:`list_dir_or_file` returns the path relative to ``dir_path``.

        Args:
            dir_path (str | Path): Path of the directory.
            list_dir (bool): List the directories. Default: True.
            list_file (bool): List the path of files. Default: True.
            suffix (str or tuple[str], optional):  File suffix
                that we are interested in. Default: None.
            recursive (bool): If set to True, recursively scan the
                directory. Default: False.
            maxnum (int): The maximum number of list. Default: 1000.

        Yields:
            Iterable[str]: A relative path to ``dir_path``.
        """
        if list_dir and suffix is not None:
            raise TypeError(
                '`list_dir` should be False when `suffix` is not None')

        if (suffix is not None) and not isinstance(suffix, (str, tuple)):
            raise TypeError('`suffix` must be a string or tuple of strings')

        bucket, obj_name = self._parse_path(dir_path)
        dir_path = obj_name

        # AWS s3's simulated directory hierarchy assumes that directory paths
        # should end with `/` if it not equal to ''.
        if dir_path and not dir_path.endswith('/'):
            dir_path += '/'

        root = dir_path

        # Used to filter duplicate folder paths
        duplicate_paths = set()

        def _list_dir_or_file(dir_path,
                              list_dir,
                              list_file,
                              suffix,
                              recursive,
                              start_token=None):
            # boto3 list method, it return json data as follows:
            # {
            #     'ResponseMetadata': {..., 'HTTPStatusCode': 200, ...},
            #     ...,
            #     'Contents': [{'Key': 'path/object', ...}, ...],
            #     ...,
            #     'NextContinuationToken': '',
            #     ...
            # }
            paginator = self._client.get_paginator('list_objects_v2')
            pagination_config = {'MaxItems': 1000, 'PageSize': 1000}
            if start_token is not None:
                pagination_config.update({'StartingToken': start_token})
            response_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=dir_path,
                PaginationConfig=pagination_config)
            next_token = None
            for response in response_iterator:
                if 'NextContinuationToken' in response:
                    next_token = response['NextContinuationToken']
                if (response['ResponseMetadata']['HTTPStatusCode'] == 200
                        and 'Contents' in response):
                    for content in response['Contents']:
                        path = content['Key'][len(root):]
                        # AWS s3 has no concept of directories, it will list
                        # all path of object from bucket. Compute folder level
                        # to distinguish different folder.
                        sparse_path = [
                            item for item in path.replace(root, '').split('/')
                            if item
                        ]
                        level = len(sparse_path)
                        if level == 0:
                            continue
                        # If recursive is False, return only one level of
                        # directory.
                        if level > 1 and not recursive:
                            if list_dir and sparse_path[
                                    0] not in duplicate_paths:
                                yield sparse_path[0] + '/'
                                duplicate_paths.add(sparse_path[0])
                            continue
                        if list_dir:
                            # Resolve the existing folder path according to
                            # the path of the object. The folder path must end
                            # with '/'.
                            for lvl in range(level - 1):
                                rel_dir = '/'.join(sparse_path[:lvl + 1])
                                if rel_dir not in duplicate_paths:
                                    yield rel_dir + '/'
                                duplicate_paths.add(rel_dir)
                        if list_file and (suffix is None
                                          or path.endswith(suffix)):
                            yield path
            if next_token is not None:
                yield from _list_dir_or_file(
                    dir_path,
                    list_dir,
                    list_file,
                    suffix,
                    recursive,
                    start_token=next_token)

        return _list_dir_or_file(dir_path, list_dir, list_file, suffix,
                                 recursive)

