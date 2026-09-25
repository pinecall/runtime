-- 0052: whether the widget draws itself light, dark, or as the visitor's browser does.

-- NULL is the widget's own default, auto, which every agent nobody set a theme for has. The door
-- takes auto, light or dark and nothing else (the protocol's WidgetTheme), so no CHECK here.

ALTER TABLE agent_widgets ADD COLUMN IF NOT EXISTS theme text;
