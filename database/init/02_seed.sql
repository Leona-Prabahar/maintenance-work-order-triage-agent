-- Maintenance work order triage — sample plant data
--
-- The queue is deliberately mixed and out of order: several safety-critical
-- reports are among the OLDEST entries, so a dashboard that simply sorted by
-- arrival time would bury them. They must still surface at the top.

INSERT INTO machines (id, machine_code, name, area, criticality) VALUES
    (1,  'HYD-PR-04',  'Hydraulic Press #4',        'Press Shop',  'Critical'),
    (2,  'CNC-M-12',   'CNC Mill #12',              'Machining',   'High'),
    (3,  'CNV-L2-A',   'Line 2 Main Conveyor',      'Assembly',    'Critical'),
    (4,  'IMM-07',     'Injection Moulder #7',      'Moulding',    'High'),
    (5,  'PNL-E3',     'Electrical Panel E3',       'Assembly',    'High'),
    (6,  'AIR-C-01',   'Plant Air Compressor #1',   'Utilities',   'Critical'),
    (7,  'RBW-02',     'Robotic Welder #2',         'Weld Cell',   'High'),
    (8,  'PKG-L4',     'Packaging Line #4',         'Packaging',   'Standard'),
    (9,  'BLR-01',     'Steam Boiler #1',           'Utilities',   'Critical'),
    (10, 'FLT-CH-02',  'Forklift Charging Bay #2',  'Warehouse',   'Standard'),
    (11, 'CNC-L-03',   'CNC Lathe #3',              'Machining',   'Standard'),
    (12, 'DUST-05',    'Dust Extraction Unit #5',   'Woodshop',    'Standard');

INSERT INTO crews (id, crew_code, name, specialty, shift, on_call, active) VALUES
    (1, 'MECH-A', 'Mechanical & Hydraulics',
        'Presses, hydraulics, pneumatics, conveyors, drives, mechanical rebuilds',
        'Day (06:00-14:00)', 0, 1),
    (2, 'ELEC-B', 'Electrical & Controls',
        'Switchgear, panels, PLC and drive faults, sensors, wiring, robot controllers',
        'Day (06:00-14:00)', 0, 1),
    (3, 'CAL-C',  'Precision & Calibration',
        'CNC calibration, tool offsets, metrology, alignment, process tuning',
        'Day (06:00-14:00)', 0, 1),
    (4, 'SRR-D',  'Safety Rapid Response',
        'Machine guarding, interlocks and light curtains, lockout/tagout, energy '
        'isolation, spill and gas response, incident containment',
        '24/7 on call', 1, 1),
    (5, 'FAC-E',  'Facilities & Utilities',
        'Boilers, steam, compressed air, HVAC, dust extraction, plant services',
        'Swing (14:00-22:00)', 0, 1);

-- --------------------------------------------------------------------------- #
-- Open queue. `reported_at` is relative to boot time so the dashboard always
-- looks like a live shift.
-- --------------------------------------------------------------------------- #
INSERT INTO work_orders
    (work_order_number, machine_id, reported_by, reporter_role, description, reported_at, status)
VALUES
    -- ---- Oldest entries: several carry injury risk ----
    ('WO-2481', 1, 'R. Okafor', 'Press Operator',
     'The light curtain on press 4 is not stopping the ram when you break the beam. '
     'Someone nearly got their hand caught on the last cycle. Looks like the guard '
     'interlock has been bypassed with a jumper.',
     NOW() - INTERVAL 355 MINUTE, 'New'),

    ('WO-2482', 2, 'M. Lindqvist', 'CNC Machinist',
     'Mill 12 is drifting about 0.04 mm on the X axis over a long program. Needs a '
     'recalibration before the next precision batch goes on.',
     NOW() - INTERVAL 331 MINUTE, 'New'),

    ('WO-2483', 3, 'D. Achterberg', 'Line Lead',
     'Line 2 main conveyor stopped mid-shift. Drive motor is humming but the belt '
     'will not move. The whole assembly line is standing.',
     NOW() - INTERVAL 298 MINUTE, 'New'),

    ('WO-2484', 8, 'P. Nakamura', 'Packaging Operator',
     'Label applicator on packaging line 4 is putting labels on slightly crooked. '
     'Line is still running, cosmetic only.',
     NOW() - INTERVAL 276 MINUTE, 'New'),

    ('WO-2485', 3, 'D. Achterberg', 'Line Lead',
     'Cover plate over the conveyor infeed has a loose bolt and rattles when the '
     'line runs. Not affecting output.',
     NOW() - INTERVAL 254 MINUTE, 'New'),

    ('WO-2486', 5, 'A. Bhatt', 'Assembly Operator',
     'Panel E3 is flickering and there is a burning smell coming off it. You can see '
     'sparks from the lower breaker when the line starts up, and there is an exposed '
     'wire behind the door.',
     NOW() - INTERVAL 221 MINUTE, 'New'),

    ('WO-2487', 4, 'S. Delgado', 'Moulding Technician',
     'Moulder 7 is throwing short shots. Barrel heater zone 3 will not hold '
     'temperature and we are scrapping nearly every part.',
     NOW() - INTERVAL 193 MINUTE, 'New'),

    ('WO-2488', 11, 'M. Lindqvist', 'CNC Machinist',
     'Coolant level is low on lathe 3. Please top it up on the next PM round.',
     NOW() - INTERVAL 168 MINUTE, 'New'),

    -- ---- A report that reads routine but contains injury risk ----
    ('WO-2489', 8, 'P. Nakamura', 'Packaging Operator',
     'The pinch point guard on the case erector is missing its retaining clip, so it '
     'swings open. Operators are reaching in to clear jams while it is running. Line '
     'output is fine.',
     NOW() - INTERVAL 141 MINUTE, 'New'),

    ('WO-2490', 9, 'T. Varga', 'Utilities Technician',
     'Steam leak on the boiler feed line, hot vapour is blowing straight across the '
     'walkway. One of the techs already caught a minor burn on his forearm getting '
     'past it.',
     NOW() - INTERVAL 118 MINUTE, 'New'),

    ('WO-2491', 6, 'T. Varga', 'Utilities Technician',
     'Plant air has dropped to 4 bar and the compressor is cycling constantly. '
     'Pneumatic tools across the shop are unusable.',
     NOW() - INTERVAL 96 MINUTE, 'New'),

    ('WO-2492', 12, 'J. Mbeki', 'Woodshop Operator',
     'Dust extraction unit 5 filter indicator is showing amber. Due for a filter '
     'change at the next opportunity.',
     NOW() - INTERVAL 74 MINUTE, 'New'),

    ('WO-2493', 7, 'K. Sorensen', 'Weld Cell Technician',
     'Robotic welder 2 is faulting on a tool centre point error and the cell is '
     'offline. No product going through the weld cell.',
     NOW() - INTERVAL 52 MINUTE, 'New'),

    ('WO-2494', 10, 'L. Fournier', 'Warehouse Lead',
     'Strong acid smell in charging bay 2. One battery casing is cracked and leaking '
     'onto the floor and the bay ventilation is not running.',
     NOW() - INTERVAL 33 MINUTE, 'New'),

    ('WO-2495', 2, 'M. Lindqvist', 'CNC Machinist',
     'Chip conveyor on mill 12 is squeaking. Could do with a lubrication when '
     'someone is passing.',
     NOW() - INTERVAL 17 MINUTE, 'New'),

    ('WO-2496', 4, 'S. Delgado', 'Moulding Technician',
     'Water hose on the mould temperature controller is weeping a little at the '
     'fitting. Small puddle forming, not yet affecting the process.',
     NOW() - INTERVAL 6 MINUTE, 'New');
