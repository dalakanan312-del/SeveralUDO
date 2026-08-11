from datetime import date, datetime, timedelta
RANGES={1:"Jan 1–Mar 31",2:"Apr 1–Jun 30",3:"Jul 1–Sep 30",4:"Oct 1–Dec 31"}
def global_day_to_year_day(global_day,start_year=1200,days_per_year=4):
    g=int(global_day); return start_year+(g-1)//days_per_year, ((g-1)%days_per_year)+1
def year_day_to_global_day(year,day,start_year=1200,days_per_year=4):
    return (int(year)-start_year)*days_per_year+int(day)
def global_day_label(global_day,start_year=1200,days_per_year=4):
    y,d=global_day_to_year_day(global_day,start_year,days_per_year); return f"{RANGES[d]}, {y}"
def date_to_global_day(date_str,start_year=1200):
    if not date_str: return None
    for fmt in ("%b %d, %Y","%B %d, %Y"):
        try:
            dt=datetime.strptime(date_str,fmt); return (dt.year-start_year)*4+((dt.month-1)//3+1)
        except: pass
    return None

def global_day_time_to_date(global_day, ingame_time, start_year=1200, days_per_year=4):
    """Map a Sim clock time within a challenge day to an exact calendar date."""
    if int(days_per_year) != 4:
        raise ValueError("Exact in-game time conversion currently requires four challenge days per year.")
    year, challenge_day = global_day_to_year_day(global_day, start_year, days_per_year)
    start_month = (challenge_day - 1) * 3 + 1
    quarter_start = date(year, start_month, 1)
    quarter_end = date(year + 1, 1, 1) if start_month == 10 else date(year, start_month + 3, 1)
    quarter_days = (quarter_end - quarter_start).days
    minutes = int(ingame_time.hour) * 60 + int(ingame_time.minute)
    offset = min(quarter_days - 1, (minutes * quarter_days) // (24 * 60))
    return quarter_start + timedelta(days=offset)

def format_exact_date(value):
    return f"{value.strftime('%B')} {value.day}, {value.year}"
