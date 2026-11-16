# Source map evidence

Recorded 2026-11-15 22:06 local time for zip 60616 (Chicago, IL), time zone America/Chicago, NWS grid LOT 76,71.

Each number under Morning and Night is how many of the 6 hours of that period the source covers, for days 1 to 7. A 0 is a gap.

| Output | Source | Field | Unit | Example value | Last hour covered | Resolution | Morning hours, days 1 to 7 | Night hours, days 1 to 7 |
|---|---|---|---|---|---|---|---|---|
| Temperature | NWS grid | temperature | wmoUnit:degC | 10 | 2026-11-22 18:00 | 1 to 5 h | 0 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Temperature | Open-Meteo | temperature_2m | °C | 0.7 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Wind speed | NWS grid | windSpeed | wmoUnit:km_h-1 | 5.556 | 2026-11-22 18:00 | 1 to 11 h | 0 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Wind speed | Open-Meteo | wind_speed_10m | km/h | 8.0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Wind gusts | NWS grid | windGust | wmoUnit:km_h-1 | 7.408 | 2026-11-22 18:00 | 1 to 11 h | 0 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Wind gusts | Open-Meteo | wind_gusts_10m | km/h | 14.4 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Visibility | NWS grid | visibility |  | NOT RETURNED |  |  |  |  |
| Visibility | Open-Meteo | visibility | m | 17500.0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| UV | NWS grid | no such layer |  | NOT RETURNED |  |  |  |  |
| UV | Open-Meteo | uv_index |  | 0.0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Humidity | NWS grid | relativeHumidity | wmoUnit:percent | 48 | 2026-11-22 18:00 | 1 to 3 h | 0 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Humidity | Open-Meteo | relative_humidity_2m | % | 83 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Rain and snow: chance | NWS grid | probabilityOfPrecipitation | wmoUnit:percent | 0 | 2026-11-22 23:00 | 6 to 87 h | 0 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Rain and snow: chance | Open-Meteo | precipitation_probability | % | 0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Rain: amount | NWS grid | quantitativePrecipitation | wmoUnit:mm | 0 | 2026-11-18 17:00 | 3 to 6 h | 0 6 6 6 0 0 0 | 6 6 6 0 0 0 0 |
| Rain: amount | Open-Meteo | precipitation | mm | 0.0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |
| Snow: amount | NWS grid | snowfallAmount | wmoUnit:mm | 0 | 2026-11-18 17:00 | 3 to 6 h | 0 6 6 6 0 0 0 | 6 6 6 0 0 0 0 |
| Snow: amount | Open-Meteo | snowfall | cm | 0.0 | 2026-11-21 23:00 | 1 h | 6 6 6 6 6 6 6 | 6 6 6 6 6 6 6 |

## Other responses

- Zip lookup fields: `['country', 'country abbreviation', 'places', 'post code']`, place fields: `['latitude', 'longitude', 'place name', 'state', 'state abbreviation']`.
- Unknown zip `00000`: HTTP status 404.
- NWS text forecast (`forecast`): last period ends 2026-11-22T18:00:00-06:00. Fields of one period: `['detailedForecast', 'endTime', 'icon', 'isDaytime', 'name', 'number', 'probabilityOfPrecipitation', 'shortForecast', 'startTime', 'temperature', 'temperatureTrend', 'temperatureUnit', 'windDirection', 'windSpeed']`.
- NWS hourly forecast (`forecastHourly`): last period ends 2026-11-22T10:00:00-06:00. Fields of one period: `['detailedForecast', 'dewpoint', 'endTime', 'icon', 'isDaytime', 'name', 'number', 'probabilityOfPrecipitation', 'relativeHumidity', 'shortForecast', 'startTime', 'temperature', 'temperatureTrend', 'temperatureUnit', 'windDirection', 'windSpeed']`.
- NWS raw grid (`forecastGridData`) layers: `['apparentTemperature', 'atmosphericDispersionIndex', 'ceilingHeight', 'davisStabilityIndex', 'dewpoint', 'dispersionIndex', 'grasslandFireDangerIndex', 'hainesIndex', 'hazards', 'heatIndex', 'heatRisk', 'iceAccumulation', 'lightningActivityLevel', 'lowVisibilityOccurrenceRiskIndex', 'maxTemperature', 'minTemperature', 'mixingHeight', 'potentialOf15mphWinds', 'potentialOf20mphWindGusts', 'potentialOf25mphWinds', 'potentialOf30mphWindGusts', 'potentialOf35mphWinds', 'potentialOf40mphWindGusts', 'potentialOf45mphWinds', 'potentialOf50mphWindGusts', 'potentialOf60mphWindGusts', 'pressure', 'primarySwellDirection', 'primarySwellHeight', 'probabilityOfHurricaneWinds', 'probabilityOfPrecipitation', 'probabilityOfThunder', 'probabilityOfTropicalStormWinds', 'quantitativePrecipitation', 'redFlagThreatIndex', 'relativeHumidity', 'secondarySwellDirection', 'secondarySwellHeight', 'skyCover', 'snowLevel', 'snowfallAmount', 'stability', 'temperature', 'transportWindDirection', 'transportWindSpeed', 'twentyFootWindDirection', 'twentyFootWindSpeed', 'visibility', 'waveDirection', 'waveHeight', 'wavePeriod', 'wavePeriod2', 'weather', 'wetBulbGlobeTemperature', 'windChill', 'windDirection', 'windGust', 'windSpeed', 'windWaveHeight']`.
- Open-Meteo units: `{'time': 'iso8601', 'temperature_2m': '°C', 'wind_speed_10m': 'km/h', 'wind_gusts_10m': 'km/h', 'visibility': 'm', 'uv_index': '', 'relative_humidity_2m': '%', 'precipitation_probability': '%', 'precipitation': 'mm', 'snowfall': 'cm'}`.
