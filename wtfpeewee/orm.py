"""
Tools for generating forms based on Peewee models
(cribbed from wtforms.ext.django)
"""

from collections import namedtuple, OrderedDict
from wtforms import Form
from wtforms import fields as f
from wtforms import widgets
from wtforms import validators
from wtfpeewee.fields import FormField
from wtfpeewee.fields import HiddenIntegerField
from wtfpeewee.fields import ModelSelectField
from wtfpeewee.fields import SelectChoicesField
from wtfpeewee.fields import SelectQueryField
from wtfpeewee.fields import WPDateField
from wtfpeewee.fields import WPDateTimeField
from wtfpeewee.fields import WPTimeField
from wtfpeewee.fields import ModelListField
from wtfpeewee.forms import ModelForm
from wtfpeewee._compat import text_type

from peewee import BareField
from peewee import BigIntegerField
from peewee import BlobField
from peewee import BooleanField
from peewee import CharField
from peewee import DateField
from peewee import DateTimeField
from peewee import DecimalField
from peewee import DoubleField
from peewee import FloatField
from peewee import ForeignKeyField
from peewee import IntegerField
from peewee import PrimaryKeyField
from peewee import TextField
from peewee import TimeField
from peewee import TimestampField

__all__ = (
    'FieldInfo',
    'ModelConverter',
    'ModelForm',
    'model_fields',
    'model_form')

def handle_null_filter(data):
    if data == '':
        return None
    return data

FieldInfo = namedtuple('FieldInfo', ('name', 'field'))

class ModelConverter(object):
    defaults = OrderedDict((
        (BareField, f.TextField),
        (BigIntegerField, f.IntegerField),
        (BlobField, f.TextAreaField),
        (BooleanField, f.BooleanField),
        (CharField, f.TextField),
        (DateField, WPDateField),
        (DateTimeField, WPDateTimeField),
        (DecimalField, f.DecimalField),
        (DoubleField, f.FloatField),
        (FloatField, f.FloatField),
        (PrimaryKeyField, HiddenIntegerField),     # TODO put through a separate PR just for bumping this up and using OrderedDict, as it's small and quite separate.
        (IntegerField, f.IntegerField),
        (TextField, f.TextAreaField),
        (TimeField, WPTimeField),
        (TimestampField, WPDateTimeField),
    ))
    coerce_defaults = {
        BigIntegerField: int,
        CharField: text_type,
        DoubleField: float,
        FloatField: float,
        IntegerField: int,
        TextField: text_type,
    }
    required = (
        CharField,
        DateTimeField,
        ForeignKeyField,
        PrimaryKeyField,
        TextField)

    def __init__(self, additional=None, additional_coerce=None, overrides=None):
        self.converters = {ForeignKeyField: self.handle_foreign_key}
        if additional:
            self.converters.update(additional)

        self.coerce_settings = dict(self.coerce_defaults)
        if additional_coerce:
            self.coerce_settings.update(additional_coerce)

        self.overrides = overrides or {}

    def handle_foreign_key(self, model, field, **kwargs):
        if field.null:
            kwargs['allow_blank'] = True
        if field.choices is not None:
            field_obj = SelectQueryField(query=field.choices, **kwargs)
        else:
            field_obj = ModelSelectField(model=field.rel_model, **kwargs)
        return FieldInfo(field.name, field_obj)

    def convert(self, model, field, field_args):
        kwargs = {
            'label': field.verbose_name,
            'validators': [],
            'filters': [],
            'default': field.default,
            'description': field.help_text}
        if field_args:
            kwargs.update(field_args)

        if kwargs['validators']:
            # Create a copy of the list since we will be modifying it.
            kwargs['validators'] = list(kwargs['validators'])

        if field.null:
            # Treat empty string as None when converting.
            kwargs['filters'].append(handle_null_filter)
            
        if (field.null or (field.default is not None)) and not field.choices:
            # If the field can be empty, or has a default value, do not require
            # it when submitting a form.
            kwargs['validators'].append(validators.Optional())
        else:
            if isinstance(field, self.required):
                kwargs['validators'].append(validators.Required())

        if field.name in self.overrides:
            return FieldInfo(field.name, self.overrides[field.name](**kwargs))

        # Allow custom-defined Peewee field classes to define their own conversion,
        # making it so that code which calls model_form() doesn't have to have special
        # cases, especially when called for the same peewee.Model from multiple places, or
        # when called in a generic context which the end-developer has less control over,
        # such as via flask-admin.
        if hasattr(field, 'wtf_field'):
            return FieldInfo(field.name, field.wtf_field(model, **kwargs))

        for converter in self.converters:
            if isinstance(field, converter):
                return self.converters[converter](model, field, **kwargs)
        else:
            for converter in self.defaults:
                print('IN DEFAULTS?', field.name, converter, field)
                if not isinstance(field, converter):
                    # Early-continue because it simplifies reading the following code.
                    continue
                if issubclass(self.defaults[converter], f.FormField):
                    # FormField fields (i.e. for nested forms) do not support
                    # filters.
                    kwargs.pop('filters')
                if field.choices or 'choices' in kwargs:
                    choices = kwargs.pop('choices', field.choices)
                    if converter in self.coerce_settings or 'coerce' in kwargs:
                        coerce_fn = kwargs.pop('coerce',
                                               self.coerce_settings[converter])
                        allow_blank = kwargs.pop('allow_blank', field.null)
                        kwargs.update({
                            'choices': choices,
                            'coerce': coerce_fn,
                            'allow_blank': allow_blank})

                        return FieldInfo(field.name, SelectChoicesField(**kwargs))

                return FieldInfo(field.name, self.defaults[converter](**kwargs))

        raise AttributeError("There is not possible conversion "
                             "for '%s'" % type(field))

    def convert_fields(self, model, allow_pk=False, only=None, exclude=None,
                       field_args=None, include_inlines=False, depth=0):
        """
        Generate a dictionary of fields for a given Peewee model.

        See `model_form` docstring for description of parameters.
        """
        field_args = field_args or {}

        model_fields = list(model._meta.sorted_fields)
        inlines = OrderedDict((k, r) for k, r in sorted(model._meta.reverse_rel.items()))
        if not allow_pk:
            model_fields.pop(0)

        if only:
            model_fields = [x for x in model_fields if x.name in only]
            inlines = OrderedDict((k, r) for k, r in inlines.items() if k in only)
        elif exclude:
            model_fields = [x for x in model_fields if x.name not in exclude]
            inlines = OrderedDict((k, r) for k, r in inlines.items() if k not in exclude)

        field_dict = {}
        for model_field in model_fields:
            name, field = self.convert(
                model,
                model_field,
                field_args.get(model_field.name))
            field_dict[name] = field

        if include_inlines:
            for name, foreign_key_field in inlines.items():
                sub_only = None
                sub_exclude = []
                if only:
                    sub_only = [n[len(name)+1:] for n in only if n.startswith(name+'.')]
                if exclude:
                    sub_exclude = [n[len(name)+1:] for n in exclude if n.startswith(name+'.')]
                if foreign_key_field.name not in sub_exclude:
                    sub_exclude.append(foreign_key_field.name)
                inline_form = self.form(foreign_key_field.model_class, allow_pk=True,
                                        only=sub_only, exclude=sub_exclude, depth=depth+1,
                                        allow_delete=True, include_inlines=True,
                                        field_args={foreign_key_field.model_class._meta.primary_key.name:
                                                    {'validators': [validators.Optional()]}})



                field_dict[name] = ModelListField(foreign_key_field,
                                                  FormField(inline_form),
                                                  depth=depth+1)

        return field_dict
    
    def form(self, model, base_class=ModelForm, allow_pk=False, only=None, exclude=None,
             field_args=None, allow_delete=False, include_inlines=False, depth=0):
        """
        Create a wtforms Form for a given Peewee model class::
        
        Parameters as per ``model_form``
        """
        field_dict = self.convert_fields(model, allow_pk=allow_pk, only=only,
                                         exclude=exclude, field_args=field_args,
                                         include_inlines=include_inlines, depth=depth)
        form = type(model.__name__ + 'Form', (base_class, ), field_dict)
        if allow_delete:
            form.delete_ = f.BooleanField(default=False)
        form.model = model
        return form


def model_fields(*args, **kwargs):
    """
    Kept for backwards-compatibility: the code is now in ModelConvert.convert_fields().
    """
    converter = kwargs.pop('converter', None) or ModelConverter()
    return converter.convert_fields(*args, **kwargs)


def model_form(*args, **kwargs):
    """
    Create a wtforms Form for a given Peewee model class::

        from wtfpeewee.orm import model_form
        from myproject.myapp.models import User
        UserForm = model_form(User)

    :param model:
        A Peewee model class
    :param base_class:
        Base form class to extend from. Must be a ``wtforms.Form`` subclass.
    :param only:
        An optional iterable with the property names that should be included in
        the form. Only these properties will have fields.
    :param exclude:
        An optional iterable with the property names that should be excluded
        from the form. All other properties will have fields.
    :param field_args:
        An optional dictionary of field names mapping to keyword arguments used
        to construct each field object.
    :param converter:
        A converter to generate the fields based on the model properties. If
        not set, ``ModelConverter`` is used.
    :param include_inlines
        Generate inline models (from ForeignKeyFields that point to this model)
    """
    converter = kwargs.pop('converter', None) or ModelConverter()
    return (converter or ModelConverter()).form(*args, **kwargs)
