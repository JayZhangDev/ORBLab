import os
import databento as db
from dotenv import load_dotenv

load_dotenv()
client = db.Historical(os.getenv("DATABENTO_API_KEY"))

print("Downloading MES 1-min data from 2022-01-01 to 2026-08-14...")
print("This may take a minute or two...")

data = client.timeseries.get_range(
    dataset="GLBX.MDP3",
    symbols=["MES.c.0"],
    stype_in="continuous",
    schema="ohlcv-1m",
    start="2022-01-01",
    end="2026-08-14",
)

df = data.to_df()
print(f"Downloaded {len(df):,} rows")

# Save to CSV so you don't have to re-download
output_path = "data/mes_1min_2022_2026.csv"
os.makedirs("data", exist_ok=True)
df.to_csv(output_path)
print(f"Saved to {output_path}")
print(df.head())
print(df.tail())