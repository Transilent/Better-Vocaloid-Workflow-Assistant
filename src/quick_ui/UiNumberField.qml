import QtQuick
import QtQuick.Controls

TextField {
    id: field
    required property QtObject theme
    required property string fieldLabel
    required property string unit
    required property real currentValue
    required property real minimum
    required property real maximum
    required property int decimals
    property real arrowStep: Math.pow(10, -decimals)
    property int scopeKey: 0
    signal committed(int scope, real value)

    property bool dirty: false
    property int draftScope: 0
    readonly property var numberLocale: Qt.locale()
    readonly property string rangeLabel: label("范围") + " " + format(minimum) + " – " + format(maximum)
        + (unit.length ? " " + unit : "")

    function label(message) {
        return typeof quickTranslation !== "undefined" ? quickTranslation.text(message) : message;
    }
    function format(value) {
        return Number(value).toLocaleString(numberLocale, "f", decimals);
    }
    function restore() {
        dirty = false;
        text = format(currentValue);
    }
    function applyValue(value, scope) {
        if (scope === scopeKey && isFinite(value) && value >= minimum && value <= maximum) {
            if (value !== currentValue)
                committed(scope, value);
            restore();
        }
    }
    function applyDraft() {
        if (dirty && acceptableInput && !inputMethodComposing && draftScope === scopeKey)
            applyValue(parsedDraft(), draftScope);
    }
    function parsedDraft() {
        try {
            return Number.fromLocaleString(numberLocale, text);
        } catch (_) {
            return NaN;
        }
    }
    function adjust(direction, fine) {
        const step = fine ? Math.pow(10, -decimals) : arrowStep;
        const draft = dirty && acceptableInput ? parsedDraft() : NaN;
        const next = Number(((isFinite(draft) ? draft : currentValue) + direction * step).toFixed(decimals));
        applyValue(Math.max(minimum, Math.min(maximum, next)), scopeKey);
    }

    implicitHeight: Math.round(32 * theme.scale)
    leftPadding: Math.round(8 * theme.scale)
    rightPadding: unitLabel.implicitWidth + Math.round(15 * theme.scale)
    topPadding: 0
    bottomPadding: 0
    selectByMouse: true
    horizontalAlignment: TextInput.AlignHCenter
    color: theme.text
    selectionColor: theme.accent
    selectedTextColor: theme.onAccent
    font.pixelSize: Math.round(12 * theme.scale)
    font.family: "Consolas"
    inputMethodHints: Qt.ImhFormattedNumbersOnly
    validator: DoubleValidator {
        bottom: field.minimum
        top: field.maximum
        decimals: field.decimals
        notation: DoubleValidator.StandardNotation
        locale: field.numberLocale.name
    }
    Accessible.name: fieldLabel
    Accessible.description: rangeLabel + " · " + label("Enter 应用 · Esc 取消")
    ToolTip.visible: hovered || (activeFocus && dirty && !acceptableInput && !inputMethodComposing)
    ToolTip.delay: dirty && !acceptableInput ? 0 : 650
    ToolTip.text: Accessible.description
    background: Rectangle {
        radius: Math.round(6 * field.theme.scale)
        color: field.theme.base
        border.width: field.activeFocus ? 2 : 1
        border.color: field.activeFocus && field.dirty && !field.acceptableInput && !field.inputMethodComposing
            ? field.theme.error : field.activeFocus ? field.theme.accent : field.theme.line
    }
    Text {
        id: unitLabel
        anchors.right: parent.right
        anchors.rightMargin: Math.round(8 * field.theme.scale)
        anchors.verticalCenter: parent.verticalCenter
        text: field.unit
        color: field.theme.muted
        font.pixelSize: Math.round(10 * field.theme.scale)
        Accessible.ignored: true
    }

    Component.onCompleted: restore()
    onScopeKeyChanged: restore()
    onCurrentValueChanged: { if (!dirty) restore(); }
    onTextEdited: {
        if (!dirty)
            draftScope = scopeKey;
        dirty = true;
    }
    onActiveFocusChanged: {
        if (!activeFocus) {
            applyDraft();
            restore();
        } else if (focusReason === Qt.TabFocusReason || focusReason === Qt.BacktabFocusReason) {
            selectAll();
        }
    }
    Keys.onPressed: event => {
        if (inputMethodComposing || (event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)))
            return;
        if (event.key === Qt.Key_Escape && dirty) {
            restore();
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            applyDraft();
            if (!dirty)
                selectAll();
        } else if (event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
            adjust(event.key === Qt.Key_Up ? 1 : -1, !!(event.modifiers & Qt.ShiftModifier));
            selectAll();
        } else {
            return;
        }
        event.accepted = true;
    }
}
