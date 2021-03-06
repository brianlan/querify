import re
from datetime import datetime
from typing import Union, Dict, Optional, Any, List

from .errors import InvalidQuery, UnrecognizedExprType, UnrecognizedJsonableClass


JsonDictType = Dict[str, Any]
JsonType = Union[int, float, str, JsonDictType]


class ClassWithSubclassDictMeta(type):
    def __init__(cls, what, bases=None, dict=None):
        super().__init__(what, bases, dict)
        if dict is None:
            return
        is_base = dict.get('base', False)
        if is_base:
            cls.subclasses = {}
        cls.base = is_base
        cls_key = dict.get('key')
        if cls_key is not None:
            if is_base:
                all_cls_subclasses = (getattr(bcl, 'subclasses', None) for bcl in bases)
            else:
                all_cls_subclasses = [getattr(cls, 'subclasses', None)]
            all_cls_subclasses = [c for c in all_cls_subclasses if c is not None]
            if not all_cls_subclasses:
                raise KeyError('class "{}" has key ("{}") but no base class is found'
                               .format(cls.__name__, cls_key))
            for cls_subclasses in all_cls_subclasses:
                cls_existing_subcls = cls_subclasses.get(cls_key)
                if cls_existing_subcls is not None:
                    raise KeyError('key "{}" of class "{}" conflicts with class "{}"'
                                   .format(cls_key, cls.__name__, cls_existing_subcls.__name__))
                cls_subclasses[cls_key] = cls

    def __getitem__(cls, cls_key):
        cls_subclasses = getattr(cls, 'subclasses', None)
        if cls_subclasses is None:
            raise KeyError('no base class is found for class "{}"'.format(cls.__name__))
        subcls = cls_subclasses.get(cls_key)
        if subcls is None:
            raise KeyError('no class has key "{}"'.format(cls_key))
        return subcls


class ClassFromJsonWithSubclassDictMeta(ClassWithSubclassDictMeta):
    def __init__(cls, what, bases=None, dict=None):
        super().__init__(what, bases, dict)
        for bcl in bases:
            if getattr(bcl, 'final', False):
                raise TypeError('class "{}" cannot subclass from "{}" which has been marked as final'
                                .format(cls.__name__, bcl.__name__))
        is_final = dict.get('final', False)
        cls.final = is_final

    def new_from_json(cls, json: JsonType):
        for key in cls.cls_keys_from_json(json):
            try:
                subcls = cls[key]
                if subcls.final:
                    kwargs = subcls.init_args_from_json(json)
                    if kwargs is not None:
                        return subcls(**kwargs)
                    else:
                        raise UnrecognizedJsonableClass('class "{}" is marked final but no init args are defined'
                                                        .format(subcls.__name__))
                elif subcls.new_from_json is cls.new_from_json:
                    raise UnrecognizedJsonableClass('class "{}" constructs an object from json "{!r}" '
                                                    'by calling itself recursively, '
                                                    'no subclass\' construct method can be found'
                                                    .format(subcls.__name__, json))
                else:
                    return subcls.new_from_json(json)
            except (KeyError, UnrecognizedExprType):
                pass
        raise UnrecognizedJsonableClass('cannot recognize class from "{}"'.format(json))

    def cls_keys_from_json(cls, json: JsonType):
        return; yield

    def init_args_from_json(cls, json) -> Optional[Dict[str, Any]]:
        pass


class Query:
    def to_query(self, type: str):
        method = getattr(self, 'to_query_' + type, None)
        if method is None:
            raise NotImplementedError('generating {} from {!r} is not supported'.format(type, self))
        return method()

    def to_query_influx(self) -> str:
        raise NotImplementedError('generating InfluxQL from {!r} is not implemented'.format(self))

    def to_query_mysql(self) -> str:
        raise NotImplementedError('generating MySQL from {!r} is not implemented'.format(self))

    def to_query_mongo(self) -> JsonType:
        raise NotImplementedError('generating MongoDB query from {!r} is not implemented'.format(self))

    def to_query_pandas(self) -> str:
        raise NotImplementedError('generating pandas query from {!r} is not implemented.'.format(self))


class Expr(Query, metaclass=ClassFromJsonWithSubclassDictMeta):
    base = True

    @classmethod
    def from_json(cls, json: Union['Expr', JsonType]) -> 'Expr':
        if isinstance(json, cls):
            expr = json
        else:
            try:
                expr = cls.new_from_json(json)
            except UnrecognizedJsonableClass as err:
                expr = err
        if not isinstance(expr, cls):
            raise InvalidQuery('Unexpected expression type for json {!r}. Expected "{}", but got "{}({})".'
                               .format(json, cls.__name__, type(expr).__name__, expr))
        return expr

    @classmethod
    def cls_keys_from_json(cls, json: JsonType):
        if isinstance(json, dict):
            yield 'operator_expr'
        else:
            yield 'literal'

    def __iter__(self):
        return self.iter_expr()

    def iter_expr(self):
        yield self
        for sub_expr in self.iter_sub_expr():
            yield from sub_expr.iter_expr()

    def iter_sub_expr(self):
        return; yield


class LiteralExpr(Expr):
    base = True
    key = 'literal'

    def __init__(self, literal):
        super().__init__()
        if not self.validate_literal(literal):
            raise InvalidQuery('Invalid type of literal "{}". Expected "{}", but got "{}"'
                               .format(literal, self.key.__name__, type(literal).__name__))
        self.literal = literal

    def validate_literal(self, literal):
        return isinstance(self.key, type) and isinstance(literal, self.key)

    @classmethod
    def cls_keys_from_json(cls, json):
        yield type(json)

    @classmethod
    def init_args_from_json(cls, json):
        return {'literal': json}

    def __repr__(self, *args, **kwargs):
        return '{}({!r})'.format(type(self).__name__, self.literal)


class StringLiteral(LiteralExpr):
    final = True
    key = str

    def to_query_influx(self) -> str:
        return "'{}'".format(self.literal)

    def to_query_mysql(self) -> str:
        return "'{}'".format(self.literal)

    def to_query_mongo(self) -> JsonType:
        return self.literal

    def to_query_pandas(self) -> str:
        return "'{}'".format(self.literal)


class BooleanLiteral(LiteralExpr):
    final = True
    key = bool

    def to_query_influx(self) -> str:
        return repr(self.literal)

    def to_query_mysql(self) -> str:
        return repr(self.literal)

    def to_query_mongo(self) -> JsonType:
        return self.literal

    def to_query_pandas(self) -> str:
        return repr(self.literal)


class IntLiteral(LiteralExpr):
    final = True
    key = int

    def to_query_influx(self) -> str:
        return repr(self.literal)

    def to_query_mysql(self) -> str:
        return repr(self.literal)

    def to_query_mongo(self) -> JsonType:
        return self.literal

    def to_query_pandas(self) -> str:
        return repr(self.literal)


class FloatLiteral(LiteralExpr):
    final = True
    key = float

    def to_query_influx(self) -> str:
        return repr(self.literal)

    def to_query_mysql(self) -> str:
        return repr(self.literal)

    def to_query_mongo(self) -> JsonType:
        return self.literal

    def to_query_pandas(self) -> str:
        return repr(self.literal)


class DateTimeLiteral(LiteralExpr):
    final = True
    key = datetime

    def to_query_influx(self) -> str:
        return "'{:%Y-%m-%dT%H:%M:%SZ}'".format(self.literal)

    def to_query_mysql(self) -> str:
        return "'{:%Y-%m-%d %H:%M:%S}'".format(self.literal)

    def to_query_mongo(self) -> JsonType:
        return self.literal


class RegexLiteral(LiteralExpr):
    final = True
    key = 'regex'

    def validate_literal(self, literal):
        return isinstance(literal, str)

    @classmethod
    def cls_keys_from_json(cls, json):
        yield 'regex'

    def to_query_influx(self) -> str:
        return '/{}/'.format(self.literal)

    def to_query_mysql(self) -> str:
        return "'{}'".format(self.literal)

    def to_query_mongo(self):
        return re.compile(self.literal)


class SchemaLiteral(LiteralExpr):
    final = True
    key = 'schema'

    def validate_literal(self, literal):
        return isinstance(literal, str)

    @classmethod
    def cls_keys_from_json(cls, json):
        yield 'schema'

    def to_query_influx(self) -> str:
        return '"{}"'.format(self.literal)

    def to_query_mysql(self) -> str:
        return self.literal

    def to_query_mongo(self) -> JsonType:
        return self.literal

    def to_query_pandas(self) -> str:
        return self.literal


# Operator Expr
class OperatorExpr(Expr):
    base = True
    key = 'operator_expr'

    operator_influx = None
    operator_mysql = None
    operator_mongo = None
    operator_pandas = None

    @classmethod
    def normalize_eval_expr_dict(cls, filter: dict) -> dict:
        exprs = []
        for tag, tag_filter in filter.items():
            if isinstance(tag_filter, str):
                if tag_filter.startswith('/') and tag_filter.endswith('/'):
                    exprs.append({tag: {'__regex__': tag_filter[1:-1]}})
                else:
                    exprs.append({tag: {'__eq__': tag_filter}})
            elif isinstance(tag_filter, (int, float)):
                exprs.append({tag: {'__eq__': tag_filter}})
            elif isinstance(tag_filter, list):
                if tag == '__and__':
                    exprs.extend([cls.normalize_eval_expr_dict(c) for c in tag_filter])
                elif tag == '__or__':
                    exprs.append({'__or__': [cls.normalize_eval_expr_dict(c) for c in tag_filter]})
                else:
                    exprs.append({'__or__': [{tag: {'__eq__': v}} for v in tag_filter]})
            elif isinstance(tag_filter, dict):
                if tag == '__not__':
                    exprs.append({'__not__': cls.normalize_eval_expr_dict(tag_filter)})
                else:
                    for op, condition in tag_filter.items():
                        if isinstance(condition, (str, int, float, datetime)):
                            exprs.append({tag: {op: condition}})
                        elif isinstance(condition, list):
                            if op == '__in__':
                                exprs.append({'__or__': [{tag: {'__eq__': v}} for v in condition]})
                            else:
                                raise InvalidQuery('"{}" operator cannot be applied on a list.'
                                                   .format(op))
                        else:
                            raise InvalidQuery('Query condition is unrecognized: {!r}'
                                               .format(condition))
            else:
                raise InvalidQuery('Invalid query "{{ {}: {} }}". '
                                   'A tag\' filter must be of one of the following types: '
                                   'regex / string / numerical / list / a dict {{ operator: operand }}.'
                                   .format(tag, tag_filter))
        if len(exprs) > 1:
            return {'__and__': exprs}
        elif len(exprs) == 1:
            return exprs[0]
        else:
            return {}

    @classmethod
    def new_from_json(cls, json: dict):
        """
        :param json:
        :return:
        :raise: UnrecognizedExprType
        """
        json = cls.normalize_eval_expr_dict(json)
        return type(cls).new_from_json(cls, json)

    @classmethod
    def cls_keys_from_json(cls, json):
        k, v = next(iter(json.items()))
        yield k
        if isinstance(v, dict) and v:
            yield next(iter(v))


# Boolean Expr
class BooleanExpr(OperatorExpr):
    pass


class UnaryBooleanExpr(BooleanExpr):
    def __init__(self, operand: Union[Expr, JsonDictType]):
        super().__init__()
        self.operand = BooleanExpr.from_json(operand)

    @classmethod
    def init_args_from_json(cls, json):
        try:
            _, operand = next(iter(json.items()))
            return {'operand': operand}
        except StopIteration:
            pass

    def iter_sub_expr(self):
        yield self.operand

    def __repr__(self):
        return '{}(operand={})'.format(type(self).__name__, self.operand)


class Not(UnaryBooleanExpr):
    final = True
    key = '__not__'
    # operator_influx = '<not>'
    operator_mysql = 'NOT'
    operator_mongo = '$not'
    operator_pandas = '~'

    # def to_query_influx(self):
    #     return '{} {}'.format(self.operator_influx, self.operand.to_query_influx())

    def to_query_mysql(self) -> str:
        return '{} ({})'.format(self.operator_mysql, self.operand.to_query_mysql())

    def to_query_mongo(self) -> JsonType:
        tmp_mongo_query = self.operand.to_query_mongo()
        k, v = next(iter(tmp_mongo_query.items()))
        return {k: {self.operator_mongo: v}}

    def to_query_pandas(self) -> str:
        return '{}({})'.format(self.operator_pandas, self.operand.to_query_pandas())


class BinaryBooleanExpr(BooleanExpr):
    def __init__(self, left: Union[SchemaLiteral, str], right):
        super().__init__()
        self.left = SchemaLiteral.from_json(left)
        self.right = LiteralExpr.from_json(right)

    @classmethod
    def init_args_from_json(cls, json):
        try:
            left, right_expr = next(iter(json.items()))
            op, right = next(iter(right_expr.items()))
            return {'left': left, 'right': right}
        except StopIteration:
            pass

    def iter_sub_expr(self):
        yield self.left
        yield self.right

    def to_query_influx(self):
        return '{} {} {}'.format(self.left.to_query_influx(), self.operator_influx, self.right.to_query_influx())

    def to_query_mysql(self) -> str:
        return '{} {} {}'.format(self.left.to_query_mysql(), self.operator_mysql, self.right.to_query_mysql())

    def to_query_mongo(self) -> JsonType:
        return {self.left.to_query_mongo(): {self.operator_mongo: self.right.to_query_mongo()}}

    def to_query_pandas(self) -> str:
        return '{} {} {}'.format(self.left.to_query_pandas(), self.operator_pandas, self.right.to_query_pandas())

    def __repr__(self):
        return '{}(left={}, right={})'.format(type(self).__name__, self.left, self.right)


class Equal(BinaryBooleanExpr):
    final = True
    key = '__eq__'
    operator_influx = '='
    operator_mysql = '='
    operator_mongo = '$eq'
    operator_pandas = '=='


class NotEqual(BinaryBooleanExpr):
    final = True
    key = '__neq__'
    operator_influx = '!='
    operator_mysql = '<>'
    operator_mongo = '$ne'
    operator_pandas = '!='


class GreaterThan(BinaryBooleanExpr):
    final = True
    key = '__gt__'
    operator_influx = '>'
    operator_mysql = '>'
    operator_mongo = '$gt'
    operator_pandas = '>'


class GreaterThanOrEqual(BinaryBooleanExpr):
    final = True
    key = '__gte__'
    operator_influx = '>='
    operator_mysql = '>='
    operator_mongo = '$gte'
    operator_pandas = '>='


class LessThan(BinaryBooleanExpr):
    final = True
    key = '__lt__'
    operator_influx = '<'
    operator_mysql = '<'
    operator_mongo = '$lt'
    operator_pandas = '<'


class LessThanOrEqual(BinaryBooleanExpr):
    final = True
    key = '__lte__'
    operator_influx = '<='
    operator_mysql = '<='
    operator_mongo = '$lte'
    operator_pandas = '<='


class MatchRegex(BinaryBooleanExpr):
    final = True
    key = '__regex__'
    operator_influx = '=~'
    operator_mysql = 'REGEXP'

    def __init__(self, left, right):
        super().__init__(left, right)
        self.right = RegexLiteral.from_json(right)

    def to_query_mongo(self) -> JsonType:
        return {self.left.to_query_mongo(): self.right.to_query_mongo()}


class InverseMatchRegex(BinaryBooleanExpr):
    final = True
    key = '__iregex__'
    operator_influx = '!~'
    operator_mysql = 'NOT REGEXP'

    def __init__(self, left, right):
        super().__init__(left, right)
        self.right = RegexLiteral.from_json(right)

    def to_query_mongo(self) -> JsonType:
        return {self.left.to_query_mongo(): {'$not': self.right.to_query_mongo()}}


class Null(BinaryBooleanExpr):
    final = True
    key = '__null__'

    def __init__(self, left: Union[SchemaLiteral, str], right: BooleanLiteral):
        super().__init__(left, right)

        if not isinstance(self.right, BooleanLiteral):
            raise InvalidQuery('The operand of "{}" must be either true or false.'.format(self.key))

    def to_query_mysql(self) -> str:
        return '{} {}'.format(self.left.to_query_mysql(), 'is NULL' if self.right.literal else 'is NOT NULL')

    def to_query_mongo(self) -> JsonType:
        return {self.left.to_query_mongo(): {'$eq' if self.right.literal else '$ne': None}}

    def to_query_pandas(self) -> str:
        return '{}pandas.isnull({})'.format('' if self.right.literal else '~', self.left.to_query_pandas())


class Missing(BinaryBooleanExpr):
    final = True
    key = '__missing__'

    def __init__(self, left: Union[SchemaLiteral, str], right: BooleanLiteral):
        super().__init__(left, right)

        if not isinstance(self.right, BooleanLiteral):
            raise InvalidQuery('The operand of "{}" must be either true or false.'.format(self.key))

    def to_query_mongo(self) -> JsonType:
        return {self.left.to_query_mongo(): {'$exists': 1 if self.right.literal else -1}}


class LogicalExpr(BooleanExpr):
    def __init__(self, exprs: List[Union[BooleanExpr, JsonDictType]]):
        super().__init__()
        if not isinstance(exprs, list):
            raise InvalidQuery('The "{}" operator is not applied on a list.'.format(self.key))
        self.exprs = [BooleanExpr.from_json(e) for e in exprs]

    def to_query_influx(self):
        return ' {} '.format(self.operator_influx).join(sorted('(' + e.to_query_influx() + ')' for e in self.exprs))

    def to_query_mysql(self):
        return ' {} '.format(self.operator_mysql).join(sorted('(' + e.to_query_mysql() + ')' for e in self.exprs))

    def to_query_mongo(self) -> JsonType:
        return {self.operator_mongo: [e.to_query_mongo() for e in self.exprs]}

    def to_query_pandas(self) -> str:
        return ' {} '.format(self.operator_pandas).join(sorted('(' + e.to_query_pandas() + ')' for e in self.exprs))

    def __repr__(self):
        return '{}({!r})'.format(type(self).__name__, self.exprs)

    @classmethod
    def init_args_from_json(cls, json):
        try:
            exprs = next(iter(json.values()))
            return {'exprs': exprs}
        except StopIteration:
            pass

    def iter_sub_expr(self):
        yield from self.exprs


class And(LogicalExpr):
    final = True
    key = '__and__'
    operator_influx = 'AND'
    operator_mysql = 'AND'
    operator_mongo = '$and'
    operator_pandas = '&'


class Or(LogicalExpr):
    final = True
    key = '__or__'
    operator_influx = 'OR'
    operator_mysql = 'OR'
    operator_mongo = '$or'
    operator_pandas = '|'


# Statement
class Stmt(Query):
    pass


class Select(Stmt):
    def __init__(self, table: Union[SchemaLiteral, str],
                 retention_policy: Optional[Union[SchemaLiteral, str]] = None,
                 db: Optional[Union[SchemaLiteral, str]] = None,
                 columns: Optional[List[Union[SchemaLiteral, str]]] = None,
                 where: Optional[Union[BooleanExpr, JsonDictType]] = None):
        super().__init__()
        self.table = SchemaLiteral.from_json(table)
        self.retention_policy = retention_policy and SchemaLiteral.from_json(retention_policy)
        self.db = db and SchemaLiteral.from_json(db)
        self.columns = columns and [SchemaLiteral.from_json(c) for c in columns]
        self.where = where and BooleanExpr.from_json(where)

    def to_query_influx(self):
        if self.columns:
            ql_select = 'SELECT ' + ','.join(c.to_query_influx() for c in self.columns)
        else:
            ql_select = 'SELECT *'

        if self.db:
            if self.retention_policy:
                ql_db = self.db.to_query_influx() + '.' + self.retention_policy.to_query_influx() + '.' + self.table.to_query_influx()
            else:
                ql_db = self.db.to_query_influx() + '..' + self.table.to_query_influx()
        elif self.retention_policy:
            ql_db = self.retention_policy.to_query_influx() + '.' + self.table.to_query_influx()
        else:
            ql_db = self.table.to_query_influx()
        ql_from = ' FROM ' + ql_db

        if self.where:
            ql_where = self.where.to_query_influx()
            if ql_where:
                ql_where = ' WHERE ' + ql_where
        else:
            ql_where = ''

        return ql_select + ql_from + ql_where

    def to_query_mysql(self):
        if self.columns:
            ql_select = 'SELECT ' + ','.join(c.to_query_mysql() for c in self.columns)
        else:
            ql_select = 'SELECT *'

        if self.db:
            ql_db = self.db.to_query_mysql() + '.' + self.table.to_query_mysql()
        else:
            ql_db = self.table.to_query_mysql()
        ql_from = ' FROM ' + ql_db

        if self.where:
            ql_where = self.where.to_query_mysql()
            if ql_where:
                ql_where = ' WHERE ' + ql_where
        else:
            ql_where = ''

        return ql_select + ql_from + ql_where


class ShowTagKeys(Stmt):
    def __init__(self, measurement: Optional[Union[SchemaLiteral, str]] = None,
                 retention_policy: Optional[Union[SchemaLiteral, str]] = None,
                 db: Optional[Union[SchemaLiteral, str]] = None,
                 where: Optional[Union[BooleanExpr, JsonDictType]] = None):
        super().__init__()
        self.measurement = measurement and SchemaLiteral.from_json(measurement)
        self.retention_policy = retention_policy and SchemaLiteral.from_json(retention_policy)
        self.db = db and SchemaLiteral.from_json(db)
        self.where = where and BooleanExpr.from_json(where)

    def to_query_influx(self):
        if self.db:
            ql_on = ' ON ' + self.db.to_query_influx()
        else:
            ql_on = ''

        if self.measurement:
            if self.retention_policy:
                ql_from = ' FROM ' + self.retention_policy.to_query_influx() + '.' + self.measurement.to_query_influx()
            else:
                ql_from = ' FROM ' + self.measurement.to_query_influx()
        else:
            ql_from = ''

        if self.where:
            ql_where = self.where.to_query_influx()
            if ql_where:
                ql_where = ' WHERE ' + ql_where
        else:
            ql_where = ''

        return 'SHOW TAG KEYS' + ql_on + ql_from + ql_where


class ShowColumns(Stmt):
    def __init__(self, table: Union[SchemaLiteral, str], db: Optional[Union[SchemaLiteral, str]] = None):
        super().__init__()
        self.table = SchemaLiteral.from_json(table)
        self.db = db and SchemaLiteral.from_json(db)

    def to_query_influx(self):
        if self.db:
            ql_on = ' ON ' + self.db.to_query_influx()
        else:
            ql_on = ''

        ql_from = ' FROM ' + self.table.to_query_influx()

        return 'SHOW TAG KEYS' + ql_on + ql_from

    def to_query_mysql(self):
        if self.db:
            ql_from = ' FROM ' + self.db.to_query_mysql() + '.' + self.table.to_query_mysql()
        else:
            ql_from = ' FROM ' + self.table.to_query_mysql()

        return 'SHOW COLUMNS' + ql_from

