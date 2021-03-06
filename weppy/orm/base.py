# -*- coding: utf-8 -*-
"""
    weppy.dal.base
    --------------

    Provides base pyDAL implementation for weppy.

    :copyright: (c) 2014-2016 by Giovanni Barillari
    :license: BSD, see LICENSE for more details.
"""

import os
from pydal import DAL as _pyDAL
from pydal._globals import THREAD_LOCAL
from .._compat import copyreg
from .._internal import warn_of_deprecation
from ..datastructures import sdict
from ..pipeline import Pipe
from ..security import uuid as _uuid
from ..serializers import _pydal_json_encode, xml
from .adapters import patch_adapter
from .objects import Table, Field, Set, Row, Rows
from .helpers import TimingHandler
from .models import MetaModel, Model


class DatabasePipe(Pipe):
    def __init__(self, db):
        self.db = db

    def open(self):
        self.db._adapter.reconnect()

    def on_pipe_success(self):
        self.db.commit()

    def on_pipe_failure(self):
        self.db.rollback()

    def close(self):
        self.db._adapter.close()


class Database(_pyDAL):
    serializers = {'json': _pydal_json_encode, 'xml': xml}
    logger = None
    uuid = lambda x: _uuid()

    record_operators = {}
    execution_handlers = []

    Rows = Rows
    Row = Row

    @staticmethod
    def uri_from_config(config=None):
        if config is None or config.adapter is None:
            config = sdict(adapter="sqlite", host="dummy.db")
        if config.adapter == "<zombie>":
            return config.adapter
        if config.adapter == "sqlite" and config.host == "memory":
            return config.adapter + ":" + config.host
        uri = config.adapter + "://"
        if config.user:
            uri = uri + config.user + ":" + config.password + "@"
        uri = uri + config.host
        if config.database:
            uri += "/" + config.database
        return uri

    def __new__(cls, app, *args, **kwargs):
        config = kwargs.get('config', sdict()) or app.config.db
        uri = config.uri or Database.uri_from_config(config)
        return super(Database, cls).__new__(cls, uri, *args, **kwargs)

    def __init__(self, app, config=sdict(), pool_size=None, folder=None,
                 **kwargs):
        app.send_signal('before_database')
        self.logger = app.log
        config = config or app.config.db
        if not config.uri:
            config.uri = self.uri_from_config(config)
        self.config = config
        #: load config data
        kwargs['check_reserved'] = self.config.check_reserved or \
            kwargs.get('check_reserved', None)
        kwargs['migrate'] = self.config.auto_migrate or \
            kwargs.get('auto_migrate', True)
        kwargs['driver_args'] = self.config.driver_args or \
            kwargs.get('driver_args', None)
        kwargs['adapter_args'] = self.config.adapter_args or \
            kwargs.get('adapter_args', None)
        if kwargs.get('auto_migrate') is not None:
            del kwargs['auto_migrate']
        #: set directory
        folder = folder or 'databases'
        folder = os.path.join(app.root_path, folder)
        if not os.path.exists(folder):
            os.mkdir(folder)
        #: set pool_size
        pool_size = self.config.pool_size or pool_size or 0
        #: add timings storage if requested
        if config.store_execution_timings:
            self.execution_handlers.append(TimingHandler)
        #: finally setup pyDAL instance
        super(Database, self).__init__(
            self.config.uri, pool_size, folder, **kwargs)
        patch_adapter(self._adapter)
        Model._init_inheritable_dicts_()
        app.send_signal('after_database', database=self)

    @property
    def pipe(self):
        return DatabasePipe(self)

    @property
    def handler(self):
        warn_of_deprecation('handler', 'pipe', 'Database', 3)
        return self.pipe

    @property
    def execution_timings(self):
        return getattr(THREAD_LOCAL, '_weppydal_timings_', [])

    def define_models(self, *models):
        if len(models) == 1 and isinstance(models[0], (list, tuple)):
            models = models[0]
        for model in models:
            if not hasattr(self, model.__name__):
                # store db instance inside model
                model.db = self
                # init model
                obj = model()
                obj._define_props_()
                obj._define_relations_()
                obj._define_virtuals_()
                obj._build_rowclass_()
                # define table and store in model
                args = dict(
                    migrate=obj.migrate,
                    format=obj.format,
                    table_class=Table
                )
                model.table = self.define_table(
                    obj.tablename, *obj.fields, **args
                )
                model.table._model_ = obj
                # load user's definitions
                obj._define_()
                # set reference in db for model name
                self.__setattr__(model.__name__, obj.table)

    def where(self, query=None, ignore_common_filters=None, model=None):
        q = None
        if isinstance(query, Table):
            q = self._adapter.id_query(query)
        elif isinstance(query, Field):
            q = (query != None)
        elif isinstance(query, dict):
            icf = query.get("ignore_common_filters")
            if icf:
                ignore_common_filters = icf
        if q is None and query is not None:
            if isinstance(query, MetaModel):
                q = self._adapter.id_query(query.table)
            else:
                q = query
        return Set(
            self, q, ignore_common_filters=ignore_common_filters, model=model)


def _Database_unpickler(db_uid):
    fake_app_obj = sdict(config=sdict(db=sdict()))
    fake_app_obj.config.db.adapter = '<zombie>'
    return Database(fake_app_obj, db_uid=db_uid)


def _Database_pickler(db):
    return _Database_unpickler, (db._db_uid,)


copyreg.pickle(Database, _Database_pickler, _Database_unpickler)
