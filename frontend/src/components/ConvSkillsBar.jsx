export default function ConvSkillsBar({ tailRunId, convSkillsData, onToggleSkill }) {
  return (
    <div className="conv-skills-bar">
      <span className="conv-skills-label">This conversation:</span>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
        {convSkillsData.length === 0
          ? <span style={{ fontSize: '11px', color: '#9aa0a6', fontStyle: 'italic' }}>
              No skills yet \u2014 add some in \u2699 Settings.
            </span>
          : convSkillsData.map(skill =>
              skill.source === 'remote' ? (
                <span
                  key={skill.id}
                  className="toggle-enabled btn-sm on readonly"
                  title={skill.description + ' (remote \u2014 always active)'}
                >
                  {skill.name}
                </span>
              ) : (
                <button
                  key={skill.id}
                  className={'toggle-enabled btn-sm' + (skill.effective_enabled ? ' on' : '')}
                  title={skill.description}
                  onClick={() => onToggleSkill(tailRunId, skill)}
                >
                  {skill.name}
                </button>
              )
            )
        }
      </div>
    </div>
  )
}
