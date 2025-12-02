import streamlit as st
import pandas as pd
import math

st.set_page_config(page_title="Healthcare Airflow Calculator", layout="wide")

def load_code_standards(file):
    """
    Parses the complex 'Code References.csv' header structure.
    It looks for the row containing standard names (like '2019 CMC TABLE-4A')
    and extracts the columns belonging to each standard.
    """
    # Load raw data to find headers
    df_raw = pd.read_csv(file, header=None)
    
    # Row 0 usually contains the Standard Names (e.g., "2019 CMC...")
    header_row = df_raw.iloc[0]
    
    standards = {}
    current_std = None
    start_col = 0
    
    # Iterate through columns to find where each standard starts and ends
    for i, val in enumerate(header_row):
        if pd.notna(val) and isinstance(val, str) and "TABLE" in val.upper() or "FGI" in str(val).upper():
            if current_std:
                # Save the previous standard range
                standards[current_std] = (start_col, i)
            current_std = val
            start_col = i
    
    # Capture the last one
    if current_std:
        standards[current_std] = (start_col, len(header_row))
        
    return df_raw, standards

def extract_standard_data(df_raw, start_col, end_col):
    """
    Extracts a clean DataFrame for a specific standard.
    Assumes Row 1 contains the actual column headers (Room Name, ACH, etc.)
    """
    # Slice the dataframe vertically
    df_std = df_raw.iloc[:, start_col:end_col].copy()
    
    # Set the first row (index 1) as header
    df_std.columns = df_std.iloc[1]
    df_std = df_std.drop([0, 1]).reset_index(drop=True)
    
    # Clean up column names (remove newlines, whitespace)
    df_std.columns = [str(c).strip().replace('\n', ' ') for c in df_std.columns]
    
    # Filter out empty rows based on 'ROOM NAME'
    if 'ROOM NAME' in df_std.columns:
        df_std = df_std[df_std['ROOM NAME'].notna()]
        
    return df_std

def calculate_airflow(row, sat, standard_data):
    """
    Main calculation logic per room.
    """
    room_type = row.get('Assigned Room Type')
    
    # 1. Lookup Code Requirements
    code_req = standard_data[standard_data['ROOM NAME'] == room_type]
    
    if code_req.empty:
        return pd.Series({'Error': 'Room Type Not Found'})
    
    code_req = code_req.iloc[0]
    
    # Safe conversion helper
    def get_num(val):
        try:
            return float(val)
        except:
            return 0.0

    # 2. Extract Requirements
    min_total_ach = get_num(code_req.get('CODE MINIMUM TOTAL AIR CHANGES', 0))
    min_oa_ach = get_num(code_req.get('CODE MINIMUM OUTDOOR AIR CHANGES', 0))
    pressure_req = code_req.get('Code Pressure', 'NR')
    
    # 3. Calculate Vent Requirements
    volume = get_num(row.get('ROOM VOLUME', 0))
    min_supply_vent_cfm = (min_total_ach * volume) / 60
    min_oa_vent_cfm = (min_oa_ach * volume) / 60
    
    # 4. Calculate Thermal Requirements
    cooling_load = get_num(row.get('Envelope Gain - Cooling (BTUH)', 0))
    design_temp = get_num(code_req.get('ROOM DESIGN TEMPERATURE (COOLING)', 72))
    
    delta_t = design_temp - sat
    if delta_t <= 0: delta_t = 20 # Safety
    
    cooling_demand_cfm = cooling_load / (1.08 * delta_t)
    
    # 5. Determine Design Supply
    # Design must satisfy the highest of: Ventilation or Thermal
    design_supply = max(min_supply_vent_cfm, cooling_demand_cfm)
    
    # 6. Pressure Balance
    # Simplified logic: Fixed offset or percentage can be added here
    offset = 0
    try:
        offset = float(code_req.get('Pressurization / Room Offset (CFM)', 0))
    except:
        offset = 0

    return_cfm = 0
    exhaust_cfm = 0
    
    is_100_exhaust = str(code_req.get('100% Exhaust', 'NO')).upper() == 'YES'
    
    if is_100_exhaust:
        exhaust_cfm = design_supply - offset
    else:
        return_cfm = design_supply - offset

    return pd.Series({
        'Standard Used': room_type,
        'Min Total ACH': min_total_ach,
        'Required Vent CFM': round(min_supply_vent_cfm, 0),
        'Cooling Load CFM': round(cooling_demand_cfm, 0),
        'Design Supply CFM': round(design_supply, 0),
        'Return CFM': round(return_cfm, 0),
        'Exhaust CFM': round(exhaust_cfm, 0),
        'Pressure': pressure_req
    })

# --- UI LAYOUT ---

st.title("🏥 Healthcare Airflow Calculator")
st.markdown("Web-based version of Table 4A Calculator")

# Sidebar for Inputs
with st.sidebar:
    st.header("1. Upload Data")
    ref_file = st.file_uploader("Upload 'Code References.csv'", type=['csv'])
    load_file = st.file_uploader("Upload 'Load Software Export.csv'", type=['csv'])
    
    st.header("2. Global Settings")
    sat = st.number_input("Supply Air Temp (SAT) °F", value=55.0)

if ref_file and load_file:
    # Process References
    df_raw_ref, standards_map = load_code_standards(ref_file)
    
    st.subheader("Select Code Standard")
    selected_std_name = st.selectbox("Choose the governing code:", list(standards_map.keys()))
    
    # Extract clean dataframe for the selected standard
    start, end = standards_map[selected_std_name]
    df_std = extract_standard_data(df_raw_ref, start, end)
    
    # Process Load Data
    # Skip initial header rows if necessary. Based on your file, header is likely row 2 (index 2)
    # Adjust 'header' index if your CSV format changes
    df_load = pd.read_csv(load_file, header=2) 
    
    # Clean up load columns
    df_load.columns = [str(c).strip() for c in df_load.columns]
    
    # Filter to valid rooms
    if 'ROOM NUMBER' in df_load.columns:
        df_load = df_load[df_load['ROOM NUMBER'].notna()]
    
    # --- INTERACTIVE MAPPING ---
    st.divider()
    st.header("3. Assign Room Types")
    
    # We create a list of available room types from the Code Standard
    available_types = df_std['ROOM NAME'].dropna().unique().tolist()
    
    # Add a dropdown column to the Load Data
    # We try to auto-match if names are similar, otherwise default to first
    df_load['Assigned Room Type'] = "Select..."
    
    # Display Editable Grid
    edited_df = st.data_editor(
        df_load[['ROOM NUMBER', 'ARCH ROOM NAME', 'ROOM VOLUME', 'Envelope Gain - Cooling (BTUH)', 'Assigned Room Type']],
        column_config={
            "Assigned Room Type": st.column_config.SelectboxColumn(
                "Healthcare Room Type",
                help="Select the regulatory room type",
                width="large",
                options=available_types,
                required=True,
            )
        },
        num_rows="dynamic",
        use_container_width=True
    )
    
    # --- CALCULATION TRIGGER ---
    if st.button("Calculate Airflows"):
        st.divider()
        st.header("4. Calculation Results")
        
        # Apply calculation to every row
        results = edited_df.apply(lambda row: calculate_airflow(row, sat, df_std), axis=1)
        
        # Merge results back with room info
        final_df = pd.concat([edited_df[['ROOM NUMBER', 'ARCH ROOM NAME']], results], axis=1)
        
        st.dataframe(final_df, use_container_width=True)
        
        # Download Button
        csv = final_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="Download Schedule as CSV",
            data=csv,
            file_name='final_airflow_schedule.csv',
            mime='text/csv',
        )

else:
    st.info("Please upload both the Code References and Load Software Export files to begin.")
