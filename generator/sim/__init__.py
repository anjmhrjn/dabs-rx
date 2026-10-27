"""Synthetic hospital simulator. Every record it produces is synthetic.

Modules:
  timeline   simulation window and shift arithmetic
  hospital   units, cabinets, par levels, staff, unit transfers
  clinical   admissions, unit stays, controlled substance orders, dose times
  adc        staffing, nurse assignment, ADC transactions, inventory, counts, injected patterns
  corrupt    deliberate bad rows for the pipeline's data quality checks
"""
