from datetime import datetime, timedelta
import pandas as pd
import requests
import db_secrets

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import insert
import psycopg2; print('Successfully installed!')

engine = create_engine(f'postgresql://postgres:{db_secrets.db_password}@localhost:5432/carbon_intensity_db')

BASE_URL = 'https://api.carbonintensity.org.uk' # More attached after to retrieve the data

def get_national_intensity(start_utc: str, end_utc: str) -> pd.DataFrame:
    """Extracts and flattens historical 30-minute national carbon intensity"""
    url = f'{BASE_URL}/intensity/{start_utc}/{end_utc}' # "/intensity" pulls in the specific data from the url
    response = requests.get(url , timeout=15)

    if response.status_code != 200:
        print(f'Failed to retrieve national data: HTTP {response.status_code}')
        return pd.DataFrame()


    raw_data = response.json().get('data', [])
    records = []

    for entry in raw_data:   # One uses this format to loop through intervals
        records.append({
            'from_datetime' : pd.to_datetime(entry.get('from')),
            'to_datetime' : pd.to_datetime(entry.get('to')),
            'intensity_forecast' : entry.get('intensity', {}).get('forecast'),
            'intensity_actual' : entry.get('intensity', {}).get('actual'),
            'intensity_index' : entry.get('intensity', {}).get('index'),
        })
    df = pd.DataFrame(records)
    return df


def get_regional_data() -> pd.DataFrame:
    url = f'{BASE_URL}/regional'  # Requires no date to fetch CURRENT generation mix
    response = requests.get(url, timeout=15)

    if response.status_code != 200:
        print(f'Failed to retrieve regional data: HTTP {response.status_code}')
        return pd.DataFrame()

    regions_data = response.json().get('data', [{}])[0].get('regions', [])
    records = []

    for region in regions_data:
        region_id = region.get('regionid')
        dno_name = region.get('shortname')

        # First loop goes through all 14 regions.
        # Second loop goes inside each region to extract fuel descriptions

        for fuel in region.get('generationmix', []):
            if region['regionid'] <= 14:  # Removes aggregates (England, Wales, Scotland, GB) so 18 becomes 14.
                records.append({
                    'region_id': region_id,
                    'fuel_type': fuel.get('fuel'),
                    'generation_share_pct': fuel.get('perc')
                })  # Region Name is excluded because the regional_fuel_mix SQL fact table is too rapidly updating
    # Return must be outside all loops for complete retrieval of all regional data
    return pd.DataFrame(records)

def postgres_upsert(table, conn, keys, data_iter):
    """
    Custom loader that tells PostgreSQL to ignore
    duplicate primary keys instead of crashing
    """
    # Format data into dictionary
    data =[dict(zip(keys, row)) for row in data_iter]
    
    # Build the standard INSERT statement
    insert_stmt = insert(table.table).values(data)

    # Add the 'ON CONFLICT DO NOTHING' clause
    do_nothing_stmt = insert_stmt.on_conflict_do_nothing()

    # Execute the safe insertion
    conn.execute(do_nothing_stmt)

if __name__ == '__main__':

    # 1. To fetch 14 days of National Intensity data
    end_date = datetime.utcnow()  # Updated from utcnow() to prevent deprecation warnings
    start_date = end_date - timedelta(days=14)

    start_str = start_date.strftime("%Y-%m-%dT%H:%MZ")
    end_str = end_date.strftime("%Y-%m-%dT%H:%MZ")

    print(f'Extracting National Intensity from {start_str} to {end_str}...')
    df_national = get_national_intensity(start_str, end_str)

    print('Extracting Regional Fuel Contributions...')
    df_regional = get_regional_data()

    # Stage to local PostgreSQL database
    if not df_national.empty:
        # if_exists='append' ensures new data is added to the BOTTOM of the table
        # index=false prevents pandas from writing the row numbers (0,1,2) to the database [ELEMENTARY really...]
        df_national.to_sql('national_intensity', engine, if_exists='append', index=False, method=postgres_upsert)
        print(f'Successfully loaded ({len(df_national)} rows) into PostgreSQL [national_intensity]')

    if not df_regional.empty:
        # Add the extraction timestamp required by my composite primary key
        df_regional['timestamp'] = pd.Timestamp.now(tz='UTC')

        df_regional.to_sql('regional_fuel_mix', engine, if_exists='append', index=False, method=postgres_upsert)
        print(f'Successfully loaded ({len(df_regional)} rows) into PostgreSQL [regional_fuel_mix]')