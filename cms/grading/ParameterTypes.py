#!/usr/bin/env python3

# Contest Management System - http://cms-dev.github.io/
# Copyright © 2010-2012 Giovanni Mascellani <mascellani@poisson.phc.unipi.it>
# Copyright © 2010-2018 Stefano Maggiolo <s.maggiolo@gmail.com>
# Copyright © 2010-2012 Matteo Boscariol <boscarim@hotmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

""" A collection of parameter type descriptions supported by AWS.

These parameter types can be used to specify the accepted parameters
of a task type or a score type. These types can cover 'basic' JSON
values, as task_type_parameters and score_type_parameters are
represented by JSON objects.

"""

from abc import ABCMeta, abstractmethod

from jinja2 import Markup, Template
import typing

if typing.TYPE_CHECKING:
    from tornado.web import RequestHandler

from cms.server.jinja2_toolbox import GLOBAL_ENVIRONMENT


class ParameterType(metaclass=ABCMeta):
    """Base class for parameter types."""

    TEMPLATE: Template = None

    def __init__(self, name: str, short_name: str, description: str):
        """Initialization.

        name: name of the parameter.
        short_name: short name without spaces, used for HTML element ids.
        description: describes the usage and effect of this parameter.

        """
        self.name = name
        self.short_name = short_name
        self.description = description

    @abstractmethod
    def validate(self, value: object) -> None:
        """Validate that the passed value is syntactically appropriate.

        value: the value to test

        raise (ValueError): if the value is malformed for this parameter.

        """
        pass

    @abstractmethod
    def parse_string(self, value: str) -> object:
        """Parse the specified string and returns the parsed value.

        value: the string value to parse.

        return: the parsed value, of the type appropriate for the
            parameter type.

        raise (ValueError): if parsing fails.

        """
        pass

    def parse_handler(self, handler: "RequestHandler", prefix: str) -> object:
        """Parse relevant parameters in the handler.

        Attempts to parse any relevant parameters in the specified handler.

        handler: a handler containing
            the required parameters as arguments.
        prefix: the prefix of the relevant arguments in the handler.

        return: the parsed value, of the type appropriate for the
            parameter type.

        raise (ValueError): if parsing fails.
        raise (MissingArgumentError) if the argument is missing from the
            handler.

        """
        return self.parse_string(handler.get_argument(
            prefix + self.short_name))

    def render(
        self,
        prefix: str,
        previous_value: object | None = None,
        extra_class: str = "",
        input_id: str | None = None,
    ) -> str:
        """Generate a form snippet for this parameter type.

        prefix: prefix to add to the fields names in the form.
        previous_value: if not None, display this value as
            default.
        extra_class: additional CSS classes to add to the input/select element.
        input_id: if not None, use this id for the rendered control.

        return: HTML form for the parameter type.

        """
        # Markup avoids escaping when other templates include this.
        return Markup(
            self.TEMPLATE.render(
                parameter=self,
                prefix=prefix,
                previous_value=previous_value,
                extra_class=extra_class,
                input_id=input_id,
            )
        )


class ParameterTypeString(ParameterType):
    """Type for a string parameter."""

    TEMPLATE = GLOBAL_ENVIRONMENT.from_string("""
<input class="input{% if extra_class %} {{ extra_class }}{% endif %}" type="text"
       {% if input_id is not none %}id="{{ input_id }}"{% endif %}
       name="{{ prefix ~ parameter.short_name }}"
       value="{{ previous_value if previous_value is not none else '' }}" />
""")

    def validate(self, value):
        if not isinstance(value, str):
            raise ValueError(
                "Invalid value for string parameter %s" % self.name)

    def parse_string(self, value):
        return value


class ParameterTypeInt(ParameterType):
    """Type for an integer parameter."""

    TEMPLATE = GLOBAL_ENVIRONMENT.from_string("""
<input class="input{% if extra_class %} {{ extra_class }}{% endif %}" type="text"
       {% if input_id is not none %}id="{{ input_id }}"{% endif %}
       name="{{ prefix ~ parameter.short_name }}"
       value="{{ previous_value }}" />
""")

    def validate(self, value):
        if not isinstance(value, int):
            raise ValueError("Invalid value for int parameter %s" % self.name)

    def parse_string(self, value):
        return int(value)


class ParameterTypeOptionalInt(ParameterType):
    """Type for an optional integer parameter with a default value."""

    TEMPLATE = GLOBAL_ENVIRONMENT.from_string("""
<input class="input{% if extra_class %} {{ extra_class }}{% endif %}" type="text"
       {% if input_id is not none %}id="{{ input_id }}"{% endif %}
       name="{{ prefix ~ parameter.short_name }}"
       value="{{ previous_value if previous_value is not none else '' }}" />
""")

    def __init__(self, name: str, short_name: str, description: str, default: int):
        """Initialization.

        default: the default value to use when the parameter is missing or empty.
        """
        super().__init__(name, short_name, description)
        self.default = default

    def validate(self, value):
        if not isinstance(value, int):
            raise ValueError("Invalid value for int parameter %s" % self.name)

    def parse_string(self, value):
        if value == "" or value is None:
            return self.default
        return int(value)

    def parse_handler(self, handler: "RequestHandler", prefix: str) -> int:
        """Parse the parameter from the handler, returning default if missing."""
        try:
            value = handler.get_argument(prefix + self.short_name)
            return self.parse_string(value)
        except Exception:
            return self.default


class ParameterTypeChoice(ParameterType):
    """Type for a parameter giving a choice among a finite number of items."""

    TEMPLATE = GLOBAL_ENVIRONMENT.from_string("""
<div class="select{% if extra_class %} {{ extra_class }}{% endif %}">
  <select {% if input_id is not none %}id="{{ input_id }}"{% endif %}
          name="{{ prefix ~ parameter.short_name }}">
  {% for choice_value, choice_description in parameter.values.items() %}
    <option value="{{ choice_value }}"
            {% if choice_value == previous_value %}selected{% endif %}>
      {{ choice_description }}
    </option>
  {% endfor %}
  </select>
</div>
""")

    def __init__(self, name, short_name, description, values: dict):
        """Initialization.

        values: dictionary mapping each choice to a short description.

        """
        super().__init__(name, short_name, description)
        self.values = values

    def validate(self, value):
        # Convert to string to avoid TypeErrors on unhashable types.
        if str(value) not in self.values:
            raise ValueError("Invalid choice %s for parameter %s" %
                             (value, self.name))

    def parse_string(self, value):
        if value not in self.values:
            raise ValueError("Value %s doesn't match any allowed choice."
                             % value)
        return value


class ParameterTypeCollection(ParameterType):
    """Type of a parameter containing a tuple of sub-parameters."""

    TEMPLATE = GLOBAL_ENVIRONMENT.from_string("""
{% for subp in parameter.subparameters %}
  {% set subp_prefix = "%s%s_%d_"|format(prefix, parameter.short_name,
                                         loop.index0) %}
  {% set subp_previous_value = (previous_value[loop.index0]
                                if previous_value is not none else none) %}
  {% set subp_input_id = (
      input_id if input_id is not none and loop.index0 == 0 else
      "%s_%d"|format(input_id, loop.index0) if input_id is not none else
      subp_prefix ~ subp.short_name
  ) %}
  <div class="field is-horizontal">
    <div class="field-label is-small">
      <label class="label" for="{{ subp_input_id }}">{{ subp.name }}</label>
    </div>
    <div class="field-body">
      <div class="field">
        <div class="control">
          {{ subp.render(subp_prefix, subp_previous_value, "is-small", subp_input_id) }}
        </div>
      </div>
    </div>
  </div>
{% endfor %}
""")

    def __init__(
        self, name, short_name, description, subparameters: list[ParameterType]
    ):
        """Initialization.

        subparameters: list of types of each sub-parameter.

        """
        super().__init__(name, short_name, description)
        self.subparameters = subparameters

    def validate(self, value):
        if not isinstance(value, list):
            raise ValueError("Parameter %s should be a list" % self.name)
        if len(value) != len(self.subparameters):
            raise ValueError("Invalid value for parameter %s" % self.name)
        for subvalue, subparameter in zip(value, self.subparameters):
            subparameter.validate(subvalue)

    def parse_string(self, value):
        raise NotImplementedError(
            "parse_string is not implemented for composite parameter types.")

    def parse_handler(self, handler, prefix):
        parsed_values = []
        for i in range(len(self.subparameters)):
            new_prefix = "%s%s_%d_" % (prefix, self.short_name, i)
            parsed_values.append(
                self.subparameters[i].parse_handler(handler, new_prefix))
        return parsed_values
