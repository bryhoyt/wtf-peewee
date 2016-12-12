"""
Base ModelForm for use with Peewee ORM
"""
from wtforms import Form
from wtfpeewee.fields import ModelListField

class ModelForm(Form):
    def process(self, formdata=None, obj=None, data=None, **kwargs):
        """
        Override base Form's .process to look up `<related_name>_prefetch` before looking
        up `<related_name>`, so Peewee's prefetch() function can be used to avoid O(N+1)
        database queries. Unfortunately, Peewee's prefetch() doesn't provide a way to
        specify the attribute name, which might be cleaner, arguably.
        
        See https://github.com/coleifer/peewee/issues/963
        """
        formdata = self.meta.wrap_formdata(self, formdata)
        if data is not None:
            kwargs = dict(data, **kwargs)
        for name, field, in self._fields.items():
            if obj is not None and hasattr(obj, name):
                if isinstance(field, ModelListField) and hasattr(obj, name+'_prefetch'):
                    # This block is the only code actually added to the base Form's .process()
                    field.process(formdata, getattr(obj, name+'_prefetch'))
                else:
                    field.process(formdata, getattr(obj, name))
            elif name in kwargs:
                field.process(formdata, kwargs[name])
            else:
                field.process(formdata)

    def save_to(self, obj):
        """
        Populate and save model instance, as well as any inline models generated from
        foreign keys.
        
        Takes care not reassign any inline model IDs or change data of inline models
        with different parents, so that a hacker cannot simply modify the hidden ID
        field in the HTML form and change someone else's data.
        
        Calling `populate_obj` is unecessary if you use `save_to`, because this
        automatically populates the object's fields before saving. We do it this way
        because `populate_obj` doesn't know what to do with inline models -- there's no
        obviously right way of populating them in such a way that leaves the object in
        a sensible state.
        
        Warning: while obj is both populated and saved, and inline models are saved, you
        can't count on inline models being properly populated in memory. Specifically,
        newly-added records won't be added to the list, but this behaviour may change in
        future.
        """
        with obj._meta.database.atomic():
            self.populate_obj(obj)
            obj.save()
            self.save_inlines(self, obj)

    def save_inlines(self, parent_form, parent_obj):
        """
        Save data of any inline fields attached to the given parent_form, starting
        at the top.
        """
        parent_pk = parent_obj._get_pk_value()
        inline_fields = [f for f in parent_form if isinstance(f, ModelListField)]
        # Save inlines:
        for field in inline_fields:
            foreign_key_field = field.foreign_key_field
            submodel = foreign_key_field.model_class
            pk_name = submodel._meta.primary_key.name
            pks = [d[pk_name] for d in field.data]
            existing = {o._get_pk_value(): o for o in getattr(parent_obj, foreign_key_field.related_name)}
            
            for formfield in field.entries:
                # Populate and save objects one-by-one, not in bulk, so that any in-python
                # Peewee functionality (such as dynamics defaults) is respected.
                subform = formfield.form
                data = subform.data
                if data[pk_name] is None:
                    if data.get('delete_'):
                        continue
                    # Create a new record, and link it to parent.
                    #
                    # Rather than using .create() here, we create an instance, and
                    # populate-and-save it below. This means fewer codepaths, as well as
                    # taking advantage of .populate_obj() behaviour of not populating
                    # inlines, which .create() doesn't respect, leading to problems.
                    subobj = submodel()
                    
                    # Link to parent:
                    setattr(subobj, foreign_key_field.name, parent_pk)
                else:
                    if data[pk_name] not in existing:
                        # Attempt to set data of a child belonging to another parent.
                        # Easily possible with simple form editing, so very dangerous.
                        raise ValueError("Cannot save data to a foreign child (id {}).".format(data[pk_name]))
                    if data.get(foreign_key_field.name, parent_pk) != parent_pk:
                        # Not so easy to do, since ModelConverter doesn't generate
                        # this field in the server-side form, so it'll get here,
                        # normally. But easy enough for an unsuspecting developer to
                        # subclass ModelConverter and introduce a nasty hole.
                        raise ValueError("Cannot change parentage of a child")
                    subobj = existing[data[pk_name]]
                    if data.get('delete_'):
                        # Delete this object, but rely on model-specified CASCADE 
                        # behaviour to delete potential subinstances.
                        subobj.delete_instance()
                        subobj = None

                if subobj:
                    subform.populate_obj(subobj)
                    subobj.save()
                    self.save_inlines(subform, subobj)
