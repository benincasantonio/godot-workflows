require "yaml"
require_relative "../fastlane/play"

class PlayTest
  def assert(value)
    raise "Assertion failed" unless value
  end

  def assert_raises(type)
    begin
      yield
    rescue type
      return
    end
    raise "Expected #{type}"
  end

  class FakeClient
    attr_reader :current_edit, :aborted
    def initialize(fail: false, codes: [[2], [1], [3]])
      @fail = fail
      @codes = codes
    end
    def begin_edit(package_name:)
      raise "Authentication rejected" if @fail == :authentication
      @current_edit = true
    end
    def aab_version_codes
      raise "API unavailable" if @fail
      @codes[0]
    end
    def apks_version_codes; @codes[1]; end
    def tracks
      [Struct.new(:releases).new([Struct.new(:version_codes).new(@codes[2])])]
    end
    def abort_current_edit
      @aborted = true
      @current_edit = nil
    end
  end

  def test_newer_version_passes
    client = FakeClient.new
    PlayRelease.preflight!({"package" => "org.example.game", "version_code" => 4}, client)
    assert client.aborted
  end

  def test_used_and_older_codes_fail_and_close_edit
    [1, 2, 3].each do |code|
      client = FakeClient.new
      assert_raises(RuntimeError) do
        PlayRelease.preflight!({"package" => "org.example.game", "version_code" => code}, client)
      end
      assert client.aborted
    end
  end

  def test_api_failure_is_not_an_empty_track
    client = FakeClient.new(fail: true)
    assert_raises(RuntimeError) do
      PlayRelease.preflight!({"package" => "org.example.game", "version_code" => 4}, client)
    end
    assert client.aborted
  end

  def test_each_version_source_is_checked
    [[[9], [], []], [[], [9], []], [[], [], [9]]].each do |codes|
      client = FakeClient.new(codes: codes)
      assert_raises(RuntimeError) do
        PlayRelease.preflight!({"package" => "org.example.game", "version_code" => 9}, client)
      end
      assert client.aborted
    end
  end

  def test_authentication_failure_stops_preflight
    client = FakeClient.new(fail: :authentication)
    assert_raises(RuntimeError) do
      PlayRelease.preflight!({"package" => "org.example.game", "version_code" => 99}, client)
    end
    assert !client.current_edit
  end
end

tests = PlayTest.new
tests.test_newer_version_passes
tests.test_used_and_older_codes_fail_and_close_edit
tests.test_api_failure_is_not_an_empty_track
tests.test_each_version_source_is_checked
tests.test_authentication_failure_stops_preflight
workflow = YAML.load_file(File.expand_path("../.github/workflows/android.yml", __dir__))
steps = workflow.fetch("jobs").fetch("android").fetch("steps")
preflight = steps.index { |step| step["run"] == "bundle exec fastlane android preflight" }
build = steps.index { |step| step["name"] == "Setup Java" }
raise "Play preflight must precede build setup" unless preflight && preflight < build
steps.each do |step|
  if step.fetch("uses", "").start_with?("google-github-actions/auth@", "ruby/setup-ruby@") || step.fetch("run", "").include?("bundle exec fastlane")
    raise "Google/Fastlane must run only when publishing" unless step["if"] == "inputs.publish"
  end
  if step.fetch("uses", "").start_with?("actions/upload-artifact@")
    raise "Publishing must never upload artifacts" unless step["if"] == "${{ !inputs.publish }}"
  end
end
puts "Play version, API failure, edit cleanup, preflight order and workflow-mode guards passed."
