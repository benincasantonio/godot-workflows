require "json"
require "supply"

module PlayRelease
  def self.require_newer!(code, codes)
    highest = codes.map(&:to_i).max || 0
    raise "Version code #{code} is not newer than Google Play's #{highest}. Increase the committed preset version code." unless code > highest
  end

  def self.preflight!(metadata, client)
    client.begin_edit(package_name: metadata.fetch("package"))
    begin
      codes = client.aab_version_codes + client.apks_version_codes +
              client.tracks.flat_map { |track| Array(track.releases).flat_map { |release| Array(release.version_codes) } }
      require_newer!(metadata.fetch("version_code"), codes)
    ensure
      client.abort_current_edit if client.current_edit
    end
  end
end
