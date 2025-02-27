""" temporal.py """

# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# Standard Library
import datetime
from datetime import timedelta
from datetime import date as dtdate, datetime as datetime_type

# Third Party
import dateutil.parser  # https://stackoverflow.com/questions/48632176/python-dateutil-attributeerror-module-dateutil-has-no-attribute-parse
from dateutil.relativedelta import relativedelta
from dateutil.rrule import SU, MO, TU, WE, TH, FR, SA  # noqa F401

# Frappe modules.
import frappe
from frappe import _, throw, msgprint, ValidationError  # noqa F401

# Temporal
from temporal import core
from temporal import redis as temporal_redis  # alias to distinguish from Third Party module

# Constants
__version__ = '13.1.0'

# Epoch is the range of 'business active' dates.
EPOCH_START_YEAR = 2020
EPOCH_END_YEAR = 2050
EPOCH_START_DATE = dtdate(EPOCH_START_YEAR, 1, 1)
EPOCH_END_DATE = dtdate(EPOCH_END_YEAR, 12, 31)

# These should be considered true Min/Max for all other calculations.
MIN_YEAR = 2000
MAX_YEAR = 2201
MIN_DATE = dtdate(MIN_YEAR, 1, 1)
MAX_DATE = dtdate(MAX_YEAR, 12, 31)

# Module Typing: https://docs.python.org/3.8/library/typing.html#module-typing

WEEKDAYS = (
	{ 'name_short': 'SUN', 'name_long': 'Sunday' },
	{ 'name_short': 'MON', 'name_long': 'Monday' },
	{ 'name_short': 'TUE', 'name_long': 'Tuesday' },
	{ 'name_short': 'WED', 'name_long': 'Wednesday' },
	{ 'name_short': 'THU', 'name_long': 'Thursday' },
	{ 'name_short': 'FRI', 'name_long': 'Friday' },
	{ 'name_short': 'SAT', 'name_long': 'Saturday' },
)

WEEKDAYS_SUN0 = (
	{ 'pos': 0, 'name_short': 'SUN', 'name_long': 'Sunday' },
	{ 'pos': 1, 'name_short': 'MON', 'name_long': 'Monday' },
	{ 'pos': 2, 'name_short': 'TUE', 'name_long': 'Tuesday' },
	{ 'pos': 3, 'name_short': 'WED', 'name_long': 'Wednesday' },
	{ 'pos': 4, 'name_short': 'THU', 'name_long': 'Thursday' },
	{ 'pos': 5, 'name_short': 'FRI', 'name_long': 'Friday' },
	{ 'pos': 6, 'name_short': 'SAT', 'name_long': 'Saturday' })

WEEKDAYS_MON0 = (
	{ 'pos': 0, 'name_short': 'MON', 'name_long': 'Monday' },
	{ 'pos': 1, 'name_short': 'TUE', 'name_long': 'Tuesday' },
	{ 'pos': 2, 'name_short': 'WED', 'name_long': 'Wednesday' },
	{ 'pos': 3, 'name_short': 'THU', 'name_long': 'Thursday' },
	{ 'pos': 4, 'name_short': 'FRI', 'name_long': 'Friday' },
	{ 'pos': 5, 'name_short': 'SAT', 'name_long': 'Saturday' },
	{ 'pos': 6, 'name_short': 'SUN', 'name_long': 'Sunday' })

class ArgumentMissing(ValidationError):
	http_status_code = 500

class ArgumentType(ValidationError):
	http_status_code = 500

class TDate():
	""" A better datetime.date """
	def __init__(self, any_date):
		if not any_date:
			raise TypeError("TDate() : Class argument 'any_date' cannot be None.")
		# To prevent a lot of downstream boilerplate, going to "assume" that strings
		# passed to this class conform to "YYYY-MM-DD" format.
		if isinstance(any_date, str):
			any_date = datestr_to_date(any_date)
		if not isinstance(any_date, datetime.date):
			raise TypeError("Class argument 'any_date' must be a Python date.")
		self.date = any_date

	def __add__(self, other):
		# operator overload:  adding two TDates
		return self.date + other.date

	def __sub__(self, other):
		# operator overload: subtracting two TDates
		return self.date - other.date

	def day_of_week_int(self, zero_based=False):
		"""
		Return an integer representing Day of Week (beginning with Sunday)
		"""
		if zero_based:
			return self.date.toordinal() % 7  # Sunday being the 0th day of week
		return (self.date.toordinal() % 7) + 1  # Sunday being the 1st day of week

	def day_of_week_shortname(self):
		return WEEKDAYS_SUN0[self.day_of_week_int() - 1]['name_short']

	def day_of_week_longname(self):
		return WEEKDAYS_SUN0[self.day_of_week_int() - 1]['name_long']

	def day_of_month(self):
		return self.date.day

	def day_of_month_ordinal(self):
		return make_ordinal(self.day_of_month())

	def day_of_year(self):
		return int(self.date.strftime("%j"))  # e.g. April 1st is the 109th day in year 2020.

	def month_of_year(self):
		return self.date.month

	def month_of_year_longname(self):
		return self.date.strftime("%B")

	def year(self):
		"""
		Integer representing the calendar date's year.
		"""
		return self.date.year

	def as_date(self):
		return self.date

	def jan1(self):
		return TDate(dtdate(year=self.date.year, month=1, day=1))

	def jan1_next_year(self):
		return TDate(dtdate(year=self.date.year + 1, month=1, day=1))

	def is_between(self, from_date, to_date):
		return from_date <= self.date <= to_date

	def week_number(self):
		"""
		This function leverages the Redis cache to find the week number.
		"""
		week = get_week_by_anydate(self.as_date())
		return week.week_number

	def as_iso_string(self):
		return date_to_iso_string(self.date)

class Week():
	""" A calendar week, starting on Sunday, where the week containing January 1st is always week #1 """
	def __init__(self, week_year, week_number, set_of_days, date_start, date_end):
		self.week_year = week_year
		self.week_number = week_number
		self.week_number_str = str(self.week_number).zfill(2)
		self.days = set_of_days
		self.date_start = date_start
		self.date_end = date_end

	def list_of_day_strings(self):
		"""
		Returns self.days as a List of ISO Date Strings.
		"""
		return [ date_to_iso_string(each_date) for each_date in self.days ]

	def print(self):
		message = f"""Week Number: {self.week_number}\nYear: {self.week_year}\nWeek Number (String): {self.week_number_str}
Days: {", ".join(self.list_of_day_strings())}\nStart: {self.date_start}\nEnd: {self.date_end}"""
		print(message)

class Builder():
	"""
	This class is used to build the Temporal data (stored in Redis Cache) """

	def __init__(self, epoch_year, end_year, start_of_week='SUN'):
		""" Initialize the Builder """

		# This determines if we output additional Error Messages.
		self.debug_mode = frappe.db.get_single_value('Temporal Manager', 'debug_mode')

		if not isinstance(start_of_week, str):
			raise TypeError("Argument 'start_of_week' should be a Python String.")
		if start_of_week not in ('SUN', 'MON'):
			raise ValueError(f"Argument 'start of week' must be either 'SUN' or 'MON' (value passed was '{start_of_week}'")
		if start_of_week != 'SUN':
			raise Exception("Temporal is not-yet coded to handle weeks that begin with Monday.")

		# Starting and Ending Year
		if not epoch_year:
			gui_start_year = int(frappe.db.get_single_value('Temporal Manager', 'start_year') or 0)
			epoch_year = gui_start_year or EPOCH_START_YEAR
		if not end_year:
			gui_end_year = int(frappe.db.get_single_value('Temporal Manager', 'end_year') or 0)
			end_year = gui_end_year or EPOCH_END_YEAR
		if end_year < epoch_year:
			raise ValueError(f"Ending year {end_year} cannot be smaller than Starting year {epoch_year}")
		self.epoch_year = epoch_year
		self.end_year = end_year

		year_range = range(self.epoch_year, self.end_year + 1)  # because Python ranges are not inclusive
		self.years = tuple(year_range)
		self.weekday_names = WEEKDAYS_SUN0 if start_of_week == 'SUN' else WEEKDAYS_MON0
		self.week_dicts = []  # this will get populated as we build.

	@staticmethod
	@frappe.whitelist()
	def build_all(epoch_year=None, end_year=None, start_of_week='SUN'):
		""" Rebuild all Temporal cache key-values. """
		instance = Builder(epoch_year=epoch_year,
		                   end_year=end_year,
		                   start_of_week=start_of_week)

		instance.build_weeks()  # must happen first, so we can build years more-easily.
		instance.build_years()
		instance.build_days()

	def build_years(self):
		""" Calculate years and write to Redis. """
		temporal_redis.write_years(self.years, self.debug_mode)
		for year in self.years:
			self.build_year(year)

	def build_year(self, year):
		""" Create a dictionary of Year metadata and write to Redis. """
		date_start = dtdate(year, 1, 1)
		date_end = dtdate(year, 12, 31)
		days_in_year = (date_end - date_start).days + 1
		jan_one_dayname = date_start.strftime("%a").upper()
		year_dict = {}
		year_dict['year'] = year
		year_dict['date_start'] = date_start.strftime("%m/%d/%Y")
		year_dict['date_end'] = date_end.strftime("%m/%d/%Y")
		year_dict['days_in_year'] = days_in_year
		# What day of the week is January 1st?
		year_dict['jan_one_dayname'] = jan_one_dayname
		try:
			weekday_short_names = tuple(weekday['name_short'] for weekday in self.weekday_names)
			year_dict['jan_one_weekpos'] = weekday_short_names.index(jan_one_dayname) + 1  # because zero-based indexing
		except ValueError as ex:
			raise ValueError(f"Could not find value '{jan_one_dayname}' in tuple 'self.weekday_names' = {self.weekday_names}") from ex
		# Get the maximum week number (52 or 53)
		max_week_number = max(week['week_number'] for week in self.week_dicts if week['year'] == year)
		year_dict['max_week_number'] = max_week_number

		temporal_redis.write_single_year(year_dict, self.debug_mode)

	def build_days(self):
		start_date = dtdate(self.epoch_year, 1, 1)  # could also do self.years[0]
		end_date = dtdate(self.end_year, 12, 31)  # could also do self.years[-1]

		count = 0
		for date_foo in date_range(start_date, end_date):
			day_dict = {}
			day_dict['date'] = date_foo
			day_dict['date_as_string'] = day_dict['date'].strftime("%Y-%m-%d")
			day_dict['weekday_name'] = date_foo.strftime("%A")
			day_dict['weekday_name_short'] = date_foo.strftime("%a")
			day_dict['day_of_month'] = date_foo.strftime("%d")
			day_dict['month_in_year_int'] = date_foo.strftime("%m")
			day_dict['month_in_year_str'] = date_foo.strftime("%B")
			day_dict['year'] = date_foo.year
			day_dict['day_of_year'] = date_foo.strftime("%j")
			# Calculate the week number:
			week_tuple = Internals.date_to_week_tuple(date_foo, verbose=False)  # previously self.debug_mode
			day_dict['week_year'] = week_tuple[0]
			day_dict['week_number'] = week_tuple[1]
			day_dict['index_in_week'] = int(date_foo.strftime("%w")) + 1  # 1-based indexing
			# Write this dictionary in the Redis cache:
			temporal_redis.write_single_day(day_dict)
			count += 1
		if self.debug_mode:
			print(f"\u2713 Created {count} Temporal Day keys in Redis.")

	def build_weeks(self):
		""" Build all the weeks between Epoch Date and End Date """
		# Begin on January 1st
		jan1_date = dtdate(self.epoch_year, 1, 1)
		jan1_day_of_week = int(jan1_date.strftime("%w"))  # day of week for January 1st

		week_start_date = jan1_date - timedelta(days=jan1_day_of_week)  # if January 1st is not Sunday, back up.
		week_end_date = None
		week_number = None
		print(f"Temporal is building weeks, starting with {week_start_date}")

		if self.debug_mode:
			print(f"Processing weeks begining with calendar date: {week_start_date}")

		count = 0
		while True:
			# Stop once week_start_date's year exceeds the Maximum Year.
			if week_start_date.year > self.end_year:
				if self.debug_mode:
					print(f"Ending loop on {week_start_date}")
				break

			week_end_date = week_start_date + timedelta(days=6)
			if self.debug_mode:
				print(f"Week's end date = {week_end_date}")
			if (week_start_date.day == 1) and (week_start_date.month == 1):
				# Sunday is January 1st, it's a new year.
				week_number = 1
			elif week_end_date.year > week_start_date.year:
				# January 1st falls somewhere inside the week
				week_number = 1
			else:
				week_number += 1
			tuple_of_dates = tuple(list(date_range(week_start_date, week_end_date)))
			if self.debug_mode:
				print(f"Writing week number {week_number}")
			week_dict = {}
			week_dict['year'] = week_end_date.year
			week_dict['week_number'] = week_number
			week_dict['week_start'] = week_start_date
			week_dict['week_end'] = week_end_date
			week_dict['week_dates'] = tuple_of_dates
			temporal_redis.write_single_week(week_dict)
			self.week_dicts.append(week_dict)  # internal object in Builder, for use later in build_years

			# Increment to the Next Week
			week_start_date = week_start_date + timedelta(days=7)
			count += 1

		# Loop complete.
		if self.debug_mode:
			print(f"\u2713 Created {count} Temporal Week keys in Redis.")


class Internals():
	""" Internal functions that should not be called outside of Temporal. """
	@staticmethod
	def date_to_week_tuple(any_date, verbose=False):
		"""
		Given a calendar date, return the corresponding week number.
		This uses a special calculation, that prevents "partial weeks"
		"""
		if not isinstance(any_date, datetime.date):
			raise TypeError("Argument must be of type 'datetime.date'")

		any_date = TDate(any_date)  # recast as a Temporal TDate
		this_year = any_date.year()
		next_year = this_year + 1

		jan1 = any_date.jan1()
		jan1_next = any_date.jan1_next_year()

		if verbose:
			print("\n----Verbose Details----")
			print(f"January 1st {this_year} is the {make_ordinal(jan1.day_of_week_int())} day in the week.")
			print(f"January 1st {next_year} is the {make_ordinal(jan1_next.day_of_week_int())} day in the week.")
			print(f"Day of Week: {any_date.day_of_week_longname()} (value of {any_date.day_of_week_int()} with 1-based indexing)")
			print(f"{any_date.as_iso_string()} Distance from Jan 1st {this_year}: {(any_date-jan1).days} days")
			print(f"{any_date.as_iso_string()} Distance from Jan 1st {next_year}: {(jan1_next-any_date).days} days")

		# SCENARIO 1: January 1st
		if (any_date.day_of_month() == 1) and (any_date.month_of_year() == 1):
			return (any_date.year(), 1)
		# SCENARIO 2A: Week 1, after January 1st
		if  ( any_date.day_of_week_int() > jan1.day_of_week_int() ) and \
			( (any_date - jan1).days in range(1, 7)):
			if verbose:
				print("Scenario 2A; calendar date is part of Week 1.")
			return (any_date.year(), 1)
		# SCENARIO 2B: Week 1, before NEXT YEAR'S January 1st
		if  ( any_date.day_of_week_int() < jan1_next.day_of_week_int() ) and \
			( (jan1_next - any_date).days in range(1, 7)):
			if verbose:
				print("Scenario 2B; target date near beginning of Future Week 1.")
			return (any_date.year() + 1, 1)
		# SCENARIO 3:  Find the first Sunday, then modulus 7.
		if verbose:
			print(f"Scenario 3: Target date is not in same Calendar Week as January 1st {this_year}/{next_year}")

		first_sundays_date = TDate(jan1.as_date() + relativedelta(weekday=SU))
		first_sundays_day_of_year = first_sundays_date.day_of_year()
		if first_sundays_day_of_year == 1:
			first_full_week = 1
		else:
			first_full_week = 2
		if verbose:
			print(f"Year's first Sunday is {first_sundays_date.as_iso_string()}, with day of year = {first_sundays_day_of_year}")
			print(f"First full week = {first_full_week}")

		# Formula: (( Date's Position in Year - Position of First Sunday) / 7 ) + 2
		# Why the +2 at the end?  Because +1 for modulus, and +1 because we're offset against Week #2
		delta = int(any_date.day_of_year() - first_sundays_day_of_year)
		week_number = int(delta / 7 ) + first_full_week
		return (jan1.year(), week_number)

	@staticmethod
	def get_year_from_frappedate(frappe_date):
		return int(frappe_date[:4])

# ----------------
# Public Functions
# ----------------

def localize_datetime(any_datetime, any_timezone):
	"""
	Given a naive datetime and time zone, return the localized datetime.

	Necessary because Python is -extremely- confusing when it comes to datetime + timezone.
	"""
	if not isinstance(any_datetime, datetime_type):
		raise TypeError("Argument 'any_datetime' must be a Python datetime object.")

	if any_datetime.tzinfo:
		raise Exception(f"Datetime value {any_datetime} is already localized and time zone aware (tzinfo={any_datetime.tzinfo})")

	# What kind of time zone object was passed?
	type_name = type(any_timezone).__name__

	# WARNING: DO NOT USE:  naive_datetime.astimezone(timezone).  This implicitly shifts you the UTC offset.
	if type_name == 'ZoneInfo':
		# Only available in Python 3.9+
		return any_datetime.replace(tzinfo=any_timezone)
	# Python 3.8 or earlier
	return any_timezone.localize(any_datetime)

def date_is_between(any_date, start_date, end_date, use_epochs=True):
	"""
	Returns a boolean if a date is between 2 other dates.
	The interesting part is the epoch date substitution.
	"""
	if (not use_epochs) and (not start_date):
		raise ValueError("Function 'date_is_between' cannot resolve Start Date = None, without 'use_epochs' argument.")
	if (not use_epochs) and (not end_date):
		raise ValueError("Function 'date_is_between' cannot resolve End Date = None, without 'use_epochs' argument.")

	if not start_date:
		start_date = EPOCH_START_DATE
	if not end_date:
		end_date = EPOCH_END_DATE

	any_date = any_to_date(any_date)
	start_date = any_to_date(start_date)
	end_date = any_to_date(end_date)

	return bool(start_date <= any_date <= end_date)

def date_range(start_date, end_date):
	"""
	Generator for an inclusive range of dates.
	It's very weird this isn't part of Python Standard Library or datetime  :/
	"""

	# As always, convert ERPNext strings into dates...
	start_date = any_to_date(start_date)
	end_date = any_to_date(end_date)
	# Important to add +1, otherwise the range is -not- inclusive.
	for number_of_days in range(int((end_date - start_date).days) + 1):
		yield start_date + timedelta(number_of_days)

def date_range_from_strdates(start_date_str, end_date_str):
	""" Generator for an inclusive range of date-strings. """
	if not isinstance(start_date_str, str):
		raise TypeError("Argument 'start_date_str' must be a Python string.")
	if not isinstance(end_date_str, str):
		raise TypeError("Argument 'end_date_str' must be a Python string.")
	start_date = datestr_to_date(start_date_str)
	end_date = datestr_to_date(end_date_str)
	return date_range(start_date, end_date)

def date_generator_type_1(start_date, increments_of, earliest_result_date):
	"""
	Given a start date, increment N number of days.
	First result can be no earlier than 'earliest_result_date'
	"""
	iterations = 0
	next_date = start_date
	while True:
		iterations += 1
		if (iterations == 1) and (start_date == earliest_result_date):  # On First Iteration, if dates match, yield Start Date.
			yield start_date
		else:
			next_date = next_date + timedelta(days=increments_of)
			if next_date >= earliest_result_date:
				yield next_date

def calc_future_dates(epoch_date, multiple_of_days, earliest_result_date, qty_of_result_dates):
	"""
		Purpose: Predict future dates, based on an epoch date and multiple.
		Returns: A List of Dates

		Arguments
		epoch_date:           The date from which the calculation begins.
		multiple_of_days:     In every iteration, how many days do we move forward?
		no_earlier_than:      What is earliest result date we want to see?
		qty_of_result_dates:  How many qualifying dates should this function return?
	"""
	validate_datatype('epoch_date', epoch_date, dtdate, True)
	validate_datatype('earliest_result_date', earliest_result_date, dtdate, True)

	# Convert to dates, always.
	epoch_date = any_to_date(epoch_date)
	earliest_result_date = any_to_date(earliest_result_date)
	# Validate the remaining data types.
	validate_datatype("multiple_of_days", multiple_of_days, int)
	validate_datatype("qty_of_result_dates", qty_of_result_dates, int)

	if earliest_result_date < epoch_date:
		raise ValueError(f"Earliest_result_date '{earliest_result_date}' cannot precede the epoch date ({epoch_date})")

	this_generator = date_generator_type_1(epoch_date, multiple_of_days, earliest_result_date)
	ret = []
	for _ in range(qty_of_result_dates):  # underscore because we don't actually need the index.
		ret.append(next(this_generator))
	return ret

def date_to_datekey(any_date):
	if not isinstance(any_date, datetime.date):
		raise Exception(f"Argument 'any_date' should have type 'datetime.date', not '{type(any_date)}'")
	date_as_string = any_date.strftime("%Y-%m-%d")
	return f"temporal/day/{date_as_string}"

def get_calendar_years():
	""" Fetch calendar years from Redis. """
	return temporal_redis.read_years()

def get_calendar_year(year):
	""" Fetch a Year dictionary from Redis. """
	return temporal_redis.read_single_year(year)

# ----------------
# Weeks
# ----------------

def week_to_weekkey(year, week_number):
	if not isinstance(week_number, int):
		raise TypeError("Argument 'week_number' should be a Python integer.")
	week_as_string = str(week_number).zfill(2)
	return f"temporal/week/{year}-{week_as_string}"


def get_week_by_weeknum(year, week_number):
	"""  Returns a class Week. """
	week_dict = temporal_redis.read_single_week(year, week_number, )
	if not week_dict:
		print(f"Warning: No value in Redis for year {year}, week number {week_number}.  Rebuilding...")
		Builder.build_all()
		if (not week_dict) and frappe.db.get_single_value('Temporal Manager', 'debug_mode'):
			raise KeyError(f"WARNING: Unable to find Week in Redis for year {year}, week {week_number}.")
		return None

	return Week(week_dict['year'],
	            week_dict['week_number'],
	            week_dict['week_dates'],
	            week_dict['week_start'],
	            week_dict['week_end'])


def get_week_by_anydate(any_date):
	"""
	Given a datetime date, returns a class instance 'Week'
	"""
	if not isinstance(any_date, dtdate):
		raise TypeError("Expected argument 'any_date' to be of type 'datetime.date'")

	date_dict = get_date_metadata(any_date)  # fetch from Redis
	if not date_dict:  # try to rebuild without throwing an error
		Builder.build_all()
		date_dict = get_date_metadata(any_date)  # 2nd Attempt
		if not date_dict:
			raise KeyError(f"WARNING: Unable to find Week in Temporal Redis for calendar date {any_date}.")

	result_week = get_week_by_weeknum(date_dict['week_year'], date_dict['week_number'])
	if not result_week:
		raise Exception(f"Unable to construct a Week() for calendar date {any_date} (week_year={date_dict['week_year']}, week_number={date_dict['week_number']})")
	return result_week

@frappe.whitelist()
def get_weeks_as_dict(year, from_week_num, to_week_num):
	""" Given a range of Week numbers, return a List of dictionaries.

		From Shell: bench execute --args "2021,15,20" temporal.get_weeks_as_dict

	"""
	# Convert JS strings into integers.
	year = int(year)
	from_week_num = int(from_week_num)
	to_week_num = int(to_week_num)

	if year not in range(MIN_YEAR, MAX_YEAR):
		raise Exception(f"Invalid value '{year}' for argument 'year'")
	if from_week_num not in range(1, 54):  # 53 possible week numbers.
		raise Exception(f"Invalid value '{from_week_num}' for argument 'from_week_num'")
	if to_week_num not in range(1, 54):  # 53 possible week numbers.
		raise Exception(f"Invalid value '{to_week_num}' for argument 'to_week_num'")

	weeks_list = []
	for week_num in range(from_week_num, to_week_num + 1):
		week_dict = temporal_redis.read_single_week(year, week_num)
		if week_dict:
			weeks_list.append(week_dict)

	return weeks_list


def datestr_to_week_number(date_as_string):
	""" Given a string date, return the Week Number. """
	return Internals.date_to_week_tuple(datestr_to_date(date_as_string), verbose=False)


def week_generator(from_date, to_date):
	"""
	Return a Python Generator for all the weeks in a date range.
	"""
	from_date = any_to_date(from_date)
	to_date = any_to_date(to_date)

	if from_date > to_date:
		raise ValueError("Argument 'from_date' cannot be greater than argument 'to_date'")
	# If dates are the same, simply return the 1 week.
	if from_date == to_date:
		yield get_week_by_anydate(from_date)

	from_week = get_week_by_anydate(from_date)  # Class of type 'Week'
	if not from_week:
		raise Exception(f"Unable to find a Week for date {from_date}. (Temporal week_generator() and Cache)")
	to_week = get_week_by_anydate(to_date)  # Class of type 'Week'
	if not to_week:
		raise Exception(f"Unable to find a Week for date {to_date} (Temporal week_generator() and Cache)")

	# results = []

	# Determine which Week Numbers are missing.
	for year in range(from_week.week_year, to_week.week_year + 1):
		# print(f"Processing week in year {year}")
		year_dict = temporal_redis.read_single_year(year)
		# Start Index
		start_index = 0
		if year == from_week.week_year:
			start_index = from_week.week_number
		else:
			start_index = 1
		# End Index
		end_index = 0
		if year == to_week.week_year:
			end_index = to_week.week_number
		else:
			end_index = year_dict['max_week_number']

		for week_num in range(start_index, end_index + 1):
			yield get_week_by_weeknum(year, week_num)  # A class of type 'Week'


# ----------------
# OTHER
# ----------------

def get_date_metadata(any_date):
	""" This function returns a date dictionary from Redis.

		bench execute --args "{'2021-04-18'}" temporal.get_date_metadata

	 """
	if isinstance(any_date, str):
		any_date = datetime.datetime.strptime(any_date, '%Y-%m-%d').date()
	if not isinstance(any_date, datetime.date):
		raise Exception(f"Argument 'any_date' should have type 'datetime.date', not '{type(any_date)}'")

	return temporal_redis.read_single_day(date_to_datekey(any_date))

def get_earliest_date(list_of_dates):
	if not all(isinstance(x, datetime.date) for x in list_of_dates):
		raise ValueError("All values in argument must be datetime dates.")
	return min(list_of_dates)

def get_latest_date(list_of_dates):
	if not all(isinstance(x, datetime.date) for x in list_of_dates):
		raise ValueError("All values in argument must be datetime dates.")
	return max(list_of_dates)

# ----------------
# DATETIME and STRING CONVERSION
# ----------------

def any_to_date(date_as_unknown):
	"""
	Given an argument of unknown Type, try to return a Date.
	"""
	try:
		if not date_as_unknown:
			return None
		if isinstance(date_as_unknown, str):
			return datetime.datetime.strptime(date_as_unknown,"%Y-%m-%d").date()
		if isinstance(date_as_unknown, datetime.date):
			return date_as_unknown

	except dateutil.parser._parser.ParserError as ex:  # pylint: disable=protected-access
		raise ValueError(f"'{date_as_unknown}' is not a valid date string.") from ex

	raise TypeError(f"Unhandled type ({type(date_as_unknown)}) for argument to function any_to_date()")

def any_to_time(generic_time):
	"""
	Given an argument of a generic, unknown Type, try to return a Time.
	"""
	try:
		if not generic_time:
			return None
		if isinstance(generic_time, str):
			return timestr_to_time(generic_time)
		if isinstance(generic_time, datetime.time):
			return generic_time

	except dateutil.parser._parser.ParserError as ex:  # pylint: disable=protected-access
		raise ValueError(f"'{generic_time}' is not a valid Time string.") from ex

	raise TypeError(f"Function argument 'generic_time' in any_to_time() has an unhandled data type: '{type(generic_time)}'")

def any_to_datetime(datetime_as_unknown):
	"""
	Given an argument of unknown Type, try to return a DateTime.
	"""
	datetime_string_format = "%Y-%m-%d %H:%M:%S"
	try:
		if not datetime_as_unknown:
			return None
		if isinstance(datetime_as_unknown, str):
			return datetime.datetime.strptime(datetime_as_unknown, datetime_string_format)
		if isinstance(datetime_as_unknown, datetime.datetime):
			return datetime_as_unknown

	except dateutil.parser._parser.ParserError as ex:  # pylint: disable=protected-access
		raise ValueError(f"'{datetime_as_unknown}' is not a valid datetime string.") from ex

	raise TypeError(f"Unhandled type ({type(datetime_as_unknown)}) for argument to function any_to_datetime()")

def any_to_iso_date_string(any_date):
	"""
	Given a date, create a String that MariaDB understands for queries (YYYY-MM-DD)
	"""
	if isinstance(any_date, datetime.date):
		return any_date.strftime("%Y-%m-%d")
	if isinstance(any_date, str):
		return any_date
	raise Exception(f"Argument 'any_date' can be a String or datetime.date only (found '{type(any_date)}')")

def datestr_to_date(date_as_string):
	"""
	Converts string date (YYYY-MM-DD) to datetime.date object.
	"""

	# ERPNext is very inconsistent with Date typing.  We should handle several possibilities:
	if not date_as_string:
		return None
	if isinstance(date_as_string, datetime.date):
		return date_as_string
	if not isinstance(date_as_string, str):
		raise TypeError(f"Argument 'date_as_string' should be of type String, not '{type(date_as_string)}'")
	if not is_date_string_valid(date_as_string):
		return None

	try:
		# Explicit is Better than Implicit.  The format should be YYYY-MM-DD.

		# The function below is completely asinine.
		# If you pass a day of week string (e.g. "Friday"), it returns the next Friday in the calendar.  Instead of an error.
		# return dateutil.parser.parse(date_as_string, yearfirst=True, dayfirst=False).date()

		# So I'm now using this instead.
		return datetime.datetime.strptime(date_as_string,"%Y-%m-%d").date()

	except dateutil.parser._parser.ParserError as ex:  # pylint: disable=protected-access
		raise ValueError("Value '{date_as_string}' is not a valid date string.") from ex

def date_to_iso_string(any_date):
	"""
	Given a date, create an ISO String.  For example, 2021-12-26.
	"""
	if not isinstance(any_date, datetime.date):
		raise Exception(f"Argument 'any_date' should have type 'datetime.date', not '{type(any_date)}'")
	return any_date.strftime("%Y-%m-%d")

def datetime_to_iso_string(any_datetime):
	"""
	Given a datetime, create a ISO String
	"""
	if not isinstance(any_datetime, datetime_type):
		raise Exception(f"Argument 'any_date' should have type 'datetime', not '{type(any_datetime)}'")

	return any_datetime.isoformat(sep=' ')  # Note: Frappe not using 'T' as a separator, but a space ''

def is_date_string_valid(date_string):
	# dateutil parser does not agree with dates like "0001-01-01" or "0000-00-00"
	if (not date_string) or (date_string or "").startswith(("0001-01-01", "0000-00-00")):
		return False
	return True

def timestr_to_time(time_as_string):
	"""
	Converts a string time (8:30pm) to datetime.time object.
	Examples:
		8pm
		830pm
		830 pm
		8:30pm
		20:30
		8:30 pm
	"""
	time_as_string = time_as_string.lower()
	time_as_string = time_as_string.replace(':', '')
	time_as_string = time_as_string.replace(' ', '')

	am_pm = None
	hour = None
	minute = None

	if 'am' in time_as_string:
		am_pm = 'am'
		time_as_string = time_as_string.replace('am', '')
	elif 'pm' in time_as_string:
		am_pm = 'pm'
		time_as_string = time_as_string.replace('pm', '')
	time_as_string = time_as_string.replace(' ', '')

	# Based on length of string, make some assumptions:
	if len(time_as_string) == 0:
		raise ValueError(f"Invalid time string '{time_as_string}'")
	if len(time_as_string) == 1:
		hour = time_as_string
		minute = 0
	elif len(time_as_string) == 2:
		raise ValueError(f"Invalid time string '{time_as_string}'")
	elif len(time_as_string) == 3:
		hour = time_as_string[0]
		minute = time_as_string[1:3]  # NOTE: Python string splicing; last index is not included.
	elif len(time_as_string) == 4:
		hour = time_as_string[0:2]  # NOTE: Python string splicing; last index is not included.
		minute = time_as_string[2:4] # NOTE: Python string splicing; last index is not included.
		if int(hour) > 12 and am_pm == 'am':
			raise ValueError(f"Invalid time string '{time_as_string}'")
	else:
		raise ValueError(f"Invalid time string '{time_as_string}'")

	if not am_pm:
		if int(hour) > 12:
			am_pm = 'pm'
		else:
			am_pm = 'am'
	if am_pm == 'pm':
		hour = int(hour) + 12

	return datetime.time(int(hour), int(minute), 0)

# ----------------
# Weekdays
# ----------------

def next_weekday_after_date(weekday, any_date):
	"""
	Find the next day of week (MON, SUN, etc) after a target date.
	"""
	weekday_int = None
	if isinstance(weekday, int):
		weekday_int = weekday
	elif isinstance(weekday, str):
		weekday_int = weekday_int_from_name(weekday, first_day_of_week='MON')  # Monday-based math below

	days_ahead = weekday_int - any_date.weekday()
	if days_ahead <= 0:  # Target day already happened this week
		days_ahead += 7
	return any_date + datetime.timedelta(days_ahead)


def validate_datatype(argument_name, argument_value, expected_type, mandatory=False):
	"""
	A helpful generic function for checking a variable's datatype, and throwing an error on mismatches.
	Absolutely necessary when dealing with extremely complex Python programs that talk to SQL, HTTP, Redis, etc.

	NOTE: expected_type can be a single Type, or a tuple of Types.
	"""
	# Throw error if missing mandatory argument.
	NoneType = type(None)
	if mandatory and isinstance(argument_value, NoneType):
		raise ArgumentMissing(f"Argument '{argument_name}' is mandatory.")

	if not argument_value:
		return argument_value  # datatype is going to be a NoneType, which is okay if not mandatory.

	# Check argument type
	if not isinstance(argument_value, expected_type):
		if isinstance(expected_type, tuple):
			expected_type_names = [ each.__name__ for each in expected_type ]
			msg = f"Argument '{argument_name}' should be one of these types: '{', '.join(expected_type_names)}'"
			msg += f"<br>Found a {type(argument_value).__name__} with value '{argument_value}' instead."
		else:
			msg = f"Argument '{argument_name}' should be of type = '{expected_type.__name__}'"
			msg += f"<br>Found a {type(argument_value).__name__} with value '{argument_value}' instead."
		raise ArgumentType(msg)

	# Otherwise, return the argument to the caller.
	return argument_value


def weekday_string_to_shortname(weekday_string):
	"""
	Given a weekday name (MON, Monday, MONDAY), convert it to the short name.
	"""
	if weekday_string.upper() in (day['name_short'] for day in WEEKDAYS):
		return weekday_string.upper()

	ret = next(day['name_short'] for day in WEEKDAYS if day['name_long'].upper() == weekday_string.upper())
	return ret


def weekday_int_from_name(weekday_name, first_day_of_week='SUN'):
	"""
	Return the position of a Weekday in a Week.
	"""
	weekday_short_name = weekday_string_to_shortname(weekday_name)
	if first_day_of_week == 'SUN':
		result = next(weekday['pos'] for weekday in WEEKDAYS_SUN0 if weekday['name_short'] == weekday_short_name)
	elif first_day_of_week == 'MON':
		result = next(weekday['pos'] for weekday in WEEKDAYS_MON0 if weekday['name_short'] == weekday_short_name)
	else:
		raise Exception("Invalid first day of week (expected SUN or MON)")
	return result


def date_to_datetime(any_date):
	"""
	Return a Date as a Datetime set to midnight.
	"""
	return datetime_type.combine(any_date, datetime_type.min.time())

def date_to_scalar(any_date):
	"""
	It makes zero difference what particular Integers we use to represent calendar dates, so long as:
		1. They are consistent throughout multiple calls/calculations.
		2. There are no gaps between calendar days.

	Given all the calendar dates stored in a Table, a simple identity column would suffice.
	"""
	scalar_value = frappe.db.get_value("Temporal Dates", filters={"calendar_date": any_date}, fieldname="scalar_value", cache=True)
	return scalar_value


def make_ordinal(some_integer) -> str:
	"""
	Convert an integer into its ordinal representation::
		make_ordinal(0)   => '0th'
		make_ordinal(3)   => '3rd'
		make_ordinal(122) => '122nd'
		make_ordinal(213) => '213th'
	"""
	# Shamelessly borrowed from here: https://stackoverflow.com/questions/9647202/ordinal-numbers-replacement
	some_integer = int(some_integer)
	if 11 <= (some_integer % 100) <= 13:
		suffix = 'th'
	else:
		suffix = ['th', 'st', 'nd', 'rd', 'th'][min(some_integer % 10, 4)]
	return str(some_integer) + suffix
