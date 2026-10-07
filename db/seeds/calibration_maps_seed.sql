-- ARGUS calibration maps seed (ARGUS-EQ-1.1, 8 horizons).
-- GENERATED from services/ml/app/registry/calibration/
-- calibration_ARGUS-EQ-1.1_h{H}.json -- do not hand-edit.
--
-- Run AFTER migrations 001-008 (needs models, model_versions,
-- calibration_maps from 004_predictions.sql).
-- Idempotent: every INSERT is ON CONFLICT DO NOTHING.

-- Prerequisite: model family row (fixed UUID).
INSERT INTO models (id, name, family, target_horizon)
VALUES ('11111111-1111-1111-1111-111111111111', 'ARGUS-EQ', 'STACKER', 20)
ON CONFLICT (id) DO NOTHING;

-- Prerequisite: champion model version row (fixed UUID).
INSERT INTO model_versions (id, model_id, version, status, promoted_at, promotion_basis)
VALUES ('22222222-2222-2222-2222-222222222222', '11111111-1111-1111-1111-111111111111', 'ARGUS-EQ-1.1', 'CHAMPION', now(),
        'walk-forward OOF: pinned regime weights + isotonic calibration maps (8 horizons)')
ON CONFLICT (id) DO NOTHING;

-- H=1: n_oof=1017, brier 0.267722 -> 0.245892
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 1, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.260162,0.260203,0.690105,0.690125,0.713865,0.714509,0.749515,0.753697,0.769286],"y":[0.25,0.25,0.536285,0.536285,0.571429,0.571429,0.642857,0.642857,1.0,1.0],"n":1017,"brier_before":0.267722,"brier_after":0.245892,"calibrated":true,"reason":""},"Bull":{"x":[0.267558,0.2895,0.504148,0.508178,0.6931,0.69367,0.696117,0.696126,0.709416,0.714509,0.741779,0.743147,0.769286],"y":[0.0,0.461538,0.461538,0.550847,0.550847,0.6,0.6,0.625,0.625,0.727273,0.727273,1.0,1.0],"n":180,"brier_before":0.256916,"brier_after":0.237713,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.595271,0.599157,0.666865,0.668046,0.681455,0.686227,0.708265,0.710069,0.743272],"y":[0.382353,0.382353,0.454545,0.454545,0.666667,0.666667,0.875,0.875,1.0,1.0],"n":60,"brier_before":0.241606,"brier_after":0.204973,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.306994,0.316419,0.690518,0.691088,0.749445],"y":[0.0,0.0,0.5311,0.5311,0.673469,0.673469],"n":264,"brier_before":0.263596,"brier_after":0.237967,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.334598,0.337324,0.681771],"y":[0.5625,0.5625,0.578947,0.578947],"n":54,"brier_before":0.315598,"brier_after":0.244457,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.272445,0.334938,0.343095,0.723474,0.724399,0.749515,0.753697,0.762572],"y":[0.25,0.25,0.536058,0.536058,0.666667,0.666667,1.0,1.0],"n":435,"brier_before":0.272688,"brier_after":0.245691,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=3: n_oof=1017, brier 0.256236 -> 0.242133
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 3, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.249929,0.260162,0.267558,0.269275,0.440633,0.441498,0.484162,0.484257,0.691236,0.691376,0.717675,0.717919,0.762572,0.769286],"y":[0.333333,0.333333,0.5,0.5,0.552326,0.552326,0.561404,0.561404,0.586441,0.586441,0.608696,0.608696,0.634615,0.634615,1.0],"n":1017,"brier_before":0.256236,"brier_after":0.242133,"calibrated":true,"reason":""},"Bull":{"x":[0.267558,0.2895,0.510781,0.511885,0.547458,0.548138,0.551858,0.553018,0.714914,0.719793,0.735658,0.739327,0.769286],"y":[0.0,0.517241,0.517241,0.55,0.55,0.6,0.6,0.637168,0.637168,0.714286,0.714286,1.0,1.0],"n":180,"brier_before":0.242507,"brier_after":0.227466,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.29034,0.599157,0.620647,0.690125,0.693143,0.743272],"y":[0.0,0.558824,0.558824,0.6,0.6,1.0,1.0],"n":60,"brier_before":0.249853,"brier_after":0.199706,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.249929,0.260162,0.306994,0.316419,0.59228,0.594733,0.600539,0.601106,0.632503,0.632942,0.749445],"y":[0.0,0.0,0.25,0.25,0.565217,0.565217,0.6,0.6,0.619048,0.619048,0.648936,0.648936],"n":264,"brier_before":0.252747,"brier_after":0.235721,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.681771],"y":[0.444444,0.444444],"n":54,"brier_before":0.288181,"brier_after":0.246914,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.272445,0.397983,0.399505,0.430354,0.43352,0.478145,0.481451,0.715895,0.716971,0.762572],"y":[0.5,0.5,0.545455,0.545455,0.551724,0.551724,0.561453,0.561453,0.652174,0.652174],"n":435,"brier_before":0.260209,"brier_after":0.245437,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=5: n_oof=1017, brier 0.257079 -> 0.242187
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 5, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.249929,0.260162,0.484162,0.484257,0.684468,0.684707,0.690518,0.690973,0.715895,0.716971,0.721383,0.722268,0.762572,0.769286],"y":[0.0,0.0,0.545064,0.545064,0.569873,0.569873,0.571429,0.571429,0.621429,0.621429,0.625,0.625,0.684211,0.684211,1.0],"n":1017,"brier_before":0.257079,"brier_after":0.242187,"calibrated":true,"reason":""},"Bull":{"x":[0.267558,0.2895,0.398334,0.408886,0.510781,0.511885,0.671371,0.672786,0.714914,0.719793,0.725508,0.735168,0.769286],"y":[0.0,0.545455,0.545455,0.555556,0.555556,0.590476,0.590476,0.636364,0.636364,0.8,0.8,1.0,1.0],"n":180,"brier_before":0.245826,"brier_after":0.22777,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.595271,0.599157,0.690125,0.693143,0.713095,0.737875,0.743272],"y":[0.529412,0.529412,0.6875,0.6875,0.875,0.875,1.0,1.0],"n":60,"brier_before":0.25151,"brier_after":0.213051,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.249929,0.260162,0.306994,0.316419,0.618124,0.618574,0.698597,0.70126,0.749445],"y":[0.0,0.0,0.25,0.25,0.535484,0.535484,0.541667,0.541667,0.645161,0.645161],"n":264,"brier_before":0.262655,"brier_after":0.243472,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.466941,0.497964,0.681771],"y":[0.0,0.5,0.5,0.555556,0.555556],"n":54,"brier_before":0.28139,"brier_after":0.244856,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.272445,0.494984,0.495684,0.680114,0.680646,0.684468,0.684707,0.762572],"y":[0.5,0.5,0.570248,0.570248,0.583333,0.583333,0.605042,0.605042],"n":435,"brier_before":0.255768,"brier_after":0.244045,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=10: n_oof=1014, brier 0.256796 -> 0.24184
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 10, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.246526,0.249929,0.260162,0.48378,0.484162,0.684286,0.684468,0.721383,0.722268,0.722536,0.722571,0.762572,0.769286],"y":[0.0,0.5,0.5,0.538793,0.538793,0.571688,0.571688,0.613757,0.613757,0.666667,0.666667,0.742857,0.742857,1.0],"n":1014,"brier_before":0.256796,"brier_after":0.24184,"calibrated":true,"reason":""},"Bull":{"x":[0.267558,0.2895,0.302873,0.512107,0.512471,0.702486,0.703228,0.719793,0.720224,0.743147,0.743432,0.769286],"y":[0.5,0.5,0.6,0.6,0.622047,0.622047,0.7,0.7,0.777778,0.777778,1.0,1.0],"n":180,"brier_before":0.248298,"brier_after":0.228966,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.29034,0.302173,0.353283,0.357796,0.633738,0.634405,0.681455,0.686227,0.698988,0.703349,0.743272],"y":[0.0,0.0,0.25,0.25,0.448276,0.448276,0.777778,0.777778,0.8,0.8,1.0,1.0],"n":60,"brier_before":0.22616,"brier_after":0.183799,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.306994,0.316419,0.695353,0.695803,0.69763,0.697687,0.736056,0.736801,0.749445],"y":[0.333333,0.333333,0.550926,0.550926,0.6,0.6,0.758621,0.758621,0.8,0.8],"n":261,"brier_before":0.262202,"brier_after":0.237868,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.402335,0.403026,0.681771],"y":[0.0,0.545455,0.545455,0.55,0.55],"n":54,"brier_before":0.293174,"brier_after":0.243182,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.272445,0.348743,0.358876,0.448611,0.451855,0.46185,0.466519,0.489698,0.490978,0.590729,0.590826,0.680114,0.680646,0.722536,0.723474,0.762572],"y":[0.375,0.375,0.40625,0.40625,0.444444,0.444444,0.5,0.5,0.538462,0.538462,0.538961,0.538961,0.582609,0.582609,0.75,0.75],"n":435,"brier_before":0.257224,"brier_after":0.244052,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=20: n_oof=1008, brier 0.248873 -> 0.234713
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 20, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.246526,0.249929,0.260162,0.48255,0.48378,0.510116,0.510781,0.513082,0.513087,0.702066,0.702486,0.722571,0.723474,0.729908,0.731559,0.735168,0.735542,0.745428,0.74543,0.769286],"y":[0.0,0.5,0.5,0.541126,0.541126,0.54902,0.54902,0.6,0.6,0.610649,0.610649,0.675325,0.675325,0.714286,0.714286,0.75,0.75,0.875,0.875,1.0,1.0],"n":1008,"brier_before":0.248873,"brier_after":0.234713,"calibrated":true,"reason":""},"Bull":{"x":[0.267558,0.302873,0.304248,0.512107,0.512471,0.701305,0.702486,0.735168,0.735658,0.743147,0.743432,0.769286],"y":[0.333333,0.333333,0.586207,0.586207,0.68254,0.68254,0.6875,0.6875,0.75,0.75,1.0,1.0],"n":180,"brier_before":0.236778,"brier_after":0.217724,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.620647,0.62096,0.633738,0.634405,0.743272],"y":[0.638889,0.638889,0.666667,0.666667,1.0,1.0],"n":60,"brier_before":0.242715,"brier_after":0.149537,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.306994,0.316419,0.509679,0.511797,0.683096,0.687659,0.702066,0.705834,0.72838,0.731559,0.749445],"y":[0.5,0.5,0.536082,0.536082,0.557692,0.557692,0.590909,0.590909,0.842105,0.842105,1.0,1.0],"n":255,"brier_before":0.253029,"brier_after":0.231851,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.33322,0.334598,0.419633,0.428941,0.681771],"y":[0.0,0.357143,0.357143,0.363636,0.363636,0.529412,0.529412],"n":54,"brier_before":0.251445,"brier_after":0.232231,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.272445,0.43495,0.43532,0.537637,0.538339,0.722536,0.723474,0.729908,0.733534,0.745428,0.745963,0.762572],"y":[0.444444,0.444444,0.465517,0.465517,0.583832,0.583832,0.75,0.75,0.857143,0.857143,1.0,1.0],"n":435,"brier_before":0.25198,"brier_after":0.238753,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=63: n_oof=981, brier 0.240301 -> 0.214872
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 63, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.249929,0.260162,0.368183,0.370925,0.46185,0.46211,0.515598,0.515735,0.708228,0.708265,0.720849,0.721383,0.728698,0.729908,0.757551,0.762572,0.769286],"y":[0.0,0.0,0.644737,0.644737,0.648438,0.648438,0.659091,0.659091,0.676617,0.676617,0.744186,0.744186,0.818182,0.818182,0.888889,0.888889,1.0,1.0],"n":981,"brier_before":0.240301,"brier_after":0.214872,"calibrated":true,"reason":""},"Bull":{"x":[0.2895,0.714509,0.714914,0.743147,0.743432,0.769286],"y":[0.786585,0.786585,0.909091,0.909091,1.0,1.0],"n":177,"brier_before":0.226386,"brier_after":0.160676,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.532069,0.545699,0.599157,0.620647,0.666865,0.668046,0.743272],"y":[0.366667,0.366667,0.8,0.8,0.9,0.9,1.0,1.0],"n":60,"brier_before":0.203092,"brier_after":0.144444,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.249929,0.260162,0.301604,0.306994,0.6973,0.69763,0.706996,0.708347,0.720849,0.721383,0.749445],"y":[0.0,0.0,0.333333,0.333333,0.654378,0.654378,0.888889,0.888889,0.916667,0.916667,1.0,1.0],"n":255,"brier_before":0.243811,"brier_after":0.202159,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.681771],"y":[0.0,0.54717,0.54717],"n":54,"brier_before":0.292165,"brier_after":0.243187,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.284443,0.358876,0.378452,0.538339,0.538559,0.623955,0.624649,0.656876,0.657038,0.728698,0.729908,0.757551,0.762572],"y":[0.428571,0.428571,0.608108,0.608108,0.613636,0.613636,0.616667,0.616667,0.639053,0.639053,0.833333,0.833333,1.0],"n":411,"brier_before":0.242241,"brier_after":0.231254,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=126: n_oof=942, brier 0.223196 -> 0.180413
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 126, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.246526,0.48378,0.484162,0.501514,0.502295,0.510781,0.511699,0.533103,0.533576,0.675483,0.675606,0.684468,0.684707,0.695399,0.695474,0.701336,0.70144,0.708228,0.708265,0.714509,0.714793,0.71997,0.720224,0.743147,0.743272,0.769286],"y":[0.0,0.685185,0.685185,0.685714,0.685714,0.6875,0.6875,0.704545,0.704545,0.748052,0.748052,0.75,0.75,0.777778,0.777778,0.833333,0.833333,0.866667,0.866667,0.88,0.88,0.916667,0.916667,0.965517,0.965517,1.0,1.0],"n":942,"brier_before":0.223196,"brier_after":0.180413,"calibrated":true,"reason":""},"Bull":{"x":[0.2895,0.533103,0.533947,0.666953,0.671371,0.743147,0.743432,0.769286],"y":[0.833333,0.833333,0.858696,0.858696,0.926829,0.926829,1.0,1.0],"n":171,"brier_before":0.19672,"brier_after":0.110781,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.29034,0.302173,0.532069,0.545699,0.62096,0.630033,0.666865,0.668046,0.743272],"y":[0.5,0.5,0.535714,0.535714,0.714286,0.714286,0.875,0.875,1.0,1.0],"n":60,"brier_before":0.221891,"brier_after":0.162798,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.246526,0.508175,0.509679,0.683096,0.687659,0.6973,0.69763,0.749445],"y":[0.696078,0.696078,0.790476,0.790476,0.8,0.8,1.0,1.0],"n":255,"brier_before":0.230174,"brier_after":0.162231,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.681771],"y":[0.0,0.54717,0.54717],"n":54,"brier_before":0.303913,"brier_after":0.243187,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.32525,0.347095,0.348743,0.538559,0.540138,0.675483,0.675606,0.695399,0.695785,0.708228,0.708394,0.71997,0.720247,0.762572],"y":[0.5,0.5,0.632353,0.632353,0.657143,0.657143,0.741379,0.741379,0.775,0.775,0.833333,0.833333,1.0,1.0],"n":378,"brier_before":0.217522,"brier_after":0.201939,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;

-- H=252: n_oof=867, brier 0.229226 -> 0.141712
INSERT INTO calibration_maps
    (model_version, horizon, method, buckets, fitted_at, min_samples)
VALUES (
    'ARGUS-EQ-1.1', 252, 'ISOTONIC',
    $${"global":{"x":[0.245817,0.260203,0.692734,0.6931,0.707292,0.707377,0.71191,0.71211,0.745963,0.749445,0.769286],"y":[0.0,0.815864,0.815864,0.818182,0.818182,0.833333,0.833333,0.949153,0.949153,1.0,1.0],"n":867,"brier_before":0.229226,"brier_after":0.141712,"calibrated":true,"reason":""},"Bull":{"x":[0.359455,0.666953,0.671371,0.705665,0.707557,0.743147,0.743432,0.769286],"y":[0.827586,0.827586,0.925926,0.925926,0.928571,0.928571,1.0,1.0],"n":159,"brier_before":0.198984,"brier_after":0.121586,"calibrated":true,"reason":"","fallback":false},"Crisis":{"fallback":true,"n":21,"calibrated":false,"reason":"n=21 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"High Vol":{"x":[0.264816,0.62096,0.630033,0.666865,0.668046,0.743272],"y":[0.783784,0.783784,0.875,0.875,1.0,1.0],"n":60,"brier_before":0.247803,"brier_after":0.119088,"calibrated":true,"reason":"","fallback":false},"Low Vol":{"fallback":true,"n":3,"calibrated":false,"reason":"n=3 < 50; thin regime -- no own map; global map not applied to this slice (uncalibrated)"},"Neutral":{"x":[0.2994,0.688765,0.68928,0.749445],"y":[0.846561,0.846561,1.0,1.0],"n":234,"brier_before":0.227601,"brier_after":0.104916,"calibrated":true,"reason":"","fallback":false},"Risk-Off":{"x":[0.245817,0.269275,0.664682,0.676183,0.681771],"y":[0.0,0.882353,0.882353,1.0,1.0],"n":54,"brier_before":0.350789,"brier_after":0.098039,"calibrated":true,"reason":"","fallback":false},"Strong Bull":{"x":[0.390381,0.71191,0.71211,0.745963,0.749515,0.762572],"y":[0.737013,0.737013,0.916667,0.916667,1.0,1.0],"n":336,"brier_before":0.220422,"brier_after":0.183129,"calibrated":true,"reason":"","fallback":false}}$$,
    '2026-10-06T19:51:06.272910+00:00'::timestamptz, 20
)
ON CONFLICT (model_version, horizon) DO NOTHING;
